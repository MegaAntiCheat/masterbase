"""Background task queue for demo operations."""

import logging
import os
import threading

import sqlalchemy as sa
from minio import Minio
from sqlalchemy import Engine

from masterbase.tasks.handlers import TaskHandler
from masterbase.tasks.compress import CompressTask, compress_demo, TASK_COMPRESS
from masterbase.tasks.analysis import AnalyzeTask, analyze_demo, TASK_ANALYZE
from masterbase.tasks.cleanup import CleanupTask, TASK_CLEANUP

logger = logging.getLogger(__name__)

# Event to signal the cleanup runner to dispatch tasks
_dispatch_event = threading.Event()

def signal_dispatch() -> None:
    """Signal the cleanup runner to run task dispatch."""
    _dispatch_event.set()

def _wait_or_timeout(timeout: float) -> None:
    """Wait for the dispatch signal or timeout. Clears the signal after waking."""
    _dispatch_event.wait(timeout=timeout)
    _dispatch_event.clear()

# Task statuses
STATUS_PENDING = "pending"
STATUS_CLAIMED = "claimed"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# Retention for completed/failed tasks (in days)
TASK_RETENTION_DAYS = 7


# Thread pool size
TASK_WORKER_THREADS = int(os.getenv("TASK_WORKER_THREADS", "4"))

# Age-based priority boost: each TASK_AGE_RATE seconds of waiting adds +1
# to the effective priority used for queue ordering. Default 60 seconds.
TASK_AGE_RATE = int(os.getenv("TASK_AGE_RATE_SECONDS", "60"))

# Sentinel session_id for singleton (global) tasks
SESSION_SINGLETON = "__singleton__"

# Task handler registry: maps task type to handler class
TASK_HANDLERS: dict[str, type] = {
    TASK_COMPRESS: CompressTask,
    TASK_ANALYZE: AnalyzeTask,
    TASK_CLEANUP: CleanupTask,
}

# Analysis config
ANALYSIS_BINARY = "/analysis-binary"
ANALYSIS_TIMEOUT = int(os.getenv("ANALYSIS_TIMEOUT", "3600"))
MEDIA_DIR = "/media"
ANALYSIS_DIR = os.path.join(MEDIA_DIR, "analysis")

def wait_for_tasks(minio_client: Minio, engine: Engine, session_id: str, dep_types: list[str]) -> bool:
    """Check if all dependency tasks are satisfied for a given session.
    
    For each dependency task type:
    1. Check the task table for a completed/skipped row (fast path cache).
    2. If found, the dependency is satisfied.
    3. If not found, run the handler's is_done() check to determine actual state.
    4. If the work is actually done but we don't have a cache row, create a skipped
       task row to cache this result for future dependency checks.
    5. If the work is not done, return False (caller should defer).
    
    Args:
        minio_client: MinIO client
        engine: Database engine
        session_id: Session ID
        dep_types: List of task type strings that must complete first
    
    Returns:
        True if all dependencies are satisfied and task can proceed.
        False if any dependency is not yet complete.
    """
    if not dep_types:
        return True
    
    for dep_type in dep_types:
        # Fast path: check task table for completed/skipped status
        status = get_task_status(engine, session_id, dep_type)
        if status in (STATUS_COMPLETED, STATUS_SKIPPED):
            continue  # Already cached as done
        
        # No cache row or not completed - do the actual check
        # Look up the handler for this dependency type
        dep_handler = TASK_HANDLERS.get(dep_type)
        if dep_handler and dep_handler.is_done(minio_client, engine, session_id):
            # Work is actually done but we don't have a completed row.
            # Create a skipped task row to cache this for future checks.
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO demo_tasks (session_id, task_type, status, completed_at, updated_at)
                        VALUES (:session_id, :task_type, :status, NOW(), NOW())
                        ON CONFLICT DO NOTHING;
                        """
                    ),
                    {
                        "session_id": session_id,
                        "task_type": dep_type,
                        "status": STATUS_SKIPPED,
                    },
                )
            logger.info(
                "Cached %s as skipped for session %s (work already done)",
                dep_type, session_id,
            )
            continue
        
        # Dependency not satisfied
        logger.debug(
            "Session %s: dependency %s not satisfied (status=%s)",
            session_id, dep_type, status,
        )
        return False
    
    return True



def enqueue_task(engine: Engine, session_id: str | None, task_type: str, priority: int = 0) -> int:
    """Add a task to the queue.

    Args:
        engine: Database engine
        session_id: Session ID to operate on. Use None for singleton tasks.
        task_type: Type of task (e.g., 'compress', 'backup')
        priority: Higher values = more urgent (default 0)

    Returns:
        Task ID
    """
    # Use sentinel for singleton tasks
    effective_session_id = session_id if session_id is not None else SESSION_SINGLETON
    
    with engine.begin() as conn:
        result = conn.execute(
            sa.text(
                """
                INSERT INTO demo_tasks (session_id, task_type, status, priority)
                VALUES (:session_id, :task_type, :status, :priority)
                ON CONFLICT DO NOTHING
                RETURNING id;
                """
            ),
            {
                "session_id": effective_session_id,
                "task_type": task_type,
                "status": STATUS_PENDING,
                "priority": priority,
            },
        )
        task_id = result.scalar_one_or_none()

    if task_id is not None:
        logger.info("Enqueued task %s for session %s (id=%s)", task_type, effective_session_id, task_id)
        signal_dispatch()
    else:
        logger.info("Task %s already queued for session %s", task_type, effective_session_id)

    return task_id or 0


def claim_task(engine: Engine, task_type: str, worker_id: str) -> dict | None:
    """Atomically claim a pending task.

    Only claims if no other task is currently running (CLAIMED) for the same session,
    ensuring only one task type runs per session at a time.

    Args:
        engine: Database engine
        task_type: Type of task to claim
        worker_id: ID of the worker claiming the task

    Returns:
        Dict with task info or None if no tasks available
    """
    with engine.begin() as conn:
        result = conn.execute(
            sa.text(
                """
                UPDATE demo_tasks
                SET status = :claimed, worker_id = :worker_id, updated_at = NOW()
                WHERE id = (
                    SELECT id FROM demo_tasks
                    WHERE status = :pending AND task_type = :task_type
                      AND session_id NOT IN (
                          SELECT session_id FROM demo_tasks
                          WHERE status = :claimed AND session_id IS NOT NULL
                      )
                    ORDER BY priority + EXTRACT(EPOCH FROM NOW() - created_at) / :age_rate DESC
                    LIMIT 1
                )
                RETURNING id, session_id, task_type, attempted_count, max_attempts;
                """
            ),
            {
                "claimed": STATUS_CLAIMED,
                "worker_id": worker_id,
                "pending": STATUS_PENDING,
                "task_type": task_type,
                "age_rate": TASK_AGE_RATE if TASK_AGE_RATE > 0 else 1,
            },
        )
        row = result.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "session_id": row[1],
        "task_type": row[2],
        "attempted_count": row[3],
        "max_attempts": row[4],
    }


def complete_task(engine: Engine, task_id: int) -> None:
    """Mark a task as completed."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                UPDATE demo_tasks
                SET status = :completed, completed_at = NOW(), updated_at = NOW()
                WHERE id = :task_id;
                """
            ),
            {"completed": STATUS_COMPLETED, "task_id": task_id},
        )
    logger.info("Task %s completed", task_id)


def skip_task(engine: Engine, task_id: int) -> None:
    """Mark a task as skipped (work was already done by another process)."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                UPDATE demo_tasks
                SET status = :skipped, completed_at = NOW(), updated_at = NOW()
                WHERE id = :task_id;
                """
            ),
            {"skipped": STATUS_SKIPPED, "task_id": task_id},
        )
    logger.info("Task %s skipped (work already done)", task_id)


def fail_task(engine: Engine, task_id: int, error: str) -> None:
    """Mark a task as failed with an error message.
    
    When re-queuing (not permanently failed), decrements priority by 10
    so repeatedly failing tasks don't jump ahead of fresh ones.
    """
    with engine.begin() as conn:
        result = conn.execute(
            sa.text(
                """
                UPDATE demo_tasks
                SET
                    attempted_count = attempted_count + 1,
                    error_message = :error,
                    updated_at = NOW(),
                    status = CASE
                        WHEN attempted_count + 1 >= max_attempts THEN :failed
                        ELSE :pending
                    END,
                    priority = CASE
                        WHEN attempted_count + 1 >= max_attempts THEN priority
                        ELSE priority - 10
                    END,
                    worker_id = NULL
                WHERE id = :task_id
                RETURNING attempted_count + 1, max_attempts;
                """
            ),
            {"error": error, "failed": STATUS_FAILED, "pending": STATUS_PENDING, "task_id": task_id},
        )
        row = result.fetchone()
        if row:
            new_count, max_attempts = row
            if new_count >= max_attempts:
                logger.warning("Task %s permanently failed after %d attempts: %s", task_id, new_count, error)
            else:
                logger.info("Task %s failed (attempt %d/%d), re-queuing: %s", task_id, new_count, max_attempts, error)


def get_task_status(engine: Engine, session_id: str, task_type: str) -> str | None:
    """Get the status of a task for a given session and type.
    
    Returns:
        Status string or None if no task exists.
    """
    with engine.connect() as conn:
        result = conn.execute(
            sa.text(
                """
                SELECT status FROM demo_tasks
                WHERE session_id = :session_id AND task_type = :task_type
                ORDER BY id DESC LIMIT 1;
                """
            ),
            {"session_id": session_id, "task_type": task_type},
        )
        row = result.fetchone()
        return row.status if row else None


def cleanup_old_tasks(engine: Engine) -> int:
    """Remove completed/failed tasks older than TASK_RETENTION_DAYS.

    Args:
        engine: Database engine

    Returns:
        Number of tasks removed.
    """
    with engine.begin() as conn:
        result = conn.execute(
            sa.text(
                """
                DELETE FROM demo_tasks
                WHERE status IN (:completed, :failed, :skipped)
                  AND completed_at < NOW() - INTERVAL :interval;
                """
            ),
            {
                "completed": STATUS_COMPLETED,
                "failed": STATUS_FAILED,
                "skipped": STATUS_SKIPPED,
                "interval": f"{TASK_RETENTION_DAYS} days",
            },
        )
        count = result.rowcount
        if count:
            logger.info("Cleaned up %d old task records.", count)
    return count


