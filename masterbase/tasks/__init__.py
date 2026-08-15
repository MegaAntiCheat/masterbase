"""Background task queue for demo operations."""

import logging
import os
import threading

import sqlalchemy as sa
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


class TaskDeferred(Exception):
    """Raised when a task resets itself to pending via depend_on().
    
    The worker catches this and skips complete/fail since the task
    was already reset to pending in the DB.
    """
    pass


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
ANALYSIS_BINARY = os.getenv("ANALYSIS_BINARY")
ANALYSIS_TIMEOUT = int(os.getenv("ANALYSIS_TIMEOUT", "3600"))
MEDIA_DIR = "/media"
ANALYSIS_DIR = os.path.join(MEDIA_DIR, "analysis")


def depend_on(
    engine: Engine, session_id: str, dep_type: str, my_task_id: int,
    wait_on_pending: bool = True
) -> bool:
    """Check if a dependency task is satisfied.
    
    If the dependency is completed, returns True.
    If the dependency is running (claimed) or pending (when wait_on_pending=True),
    ensures it's queued and resets this task to pending so the scheduler processes
    the dependency first.
    
    Args:
        engine: Database engine
        session_id: Session ID
        dep_type: Dependency task type (e.g., TASK_COMPRESS)
        my_task_id: ID of this task (to reset if waiting)
        wait_on_pending: If True, wait on both pending AND claimed tasks.
                        If False, only wait on claimed (running) tasks.
    
    Returns:
        True if dependency is satisfied and task can proceed.
        Raises TaskDeferred if task was reset to pending and should wait.
    """
    status = get_task_status(engine, session_id, dep_type)
    if status in (STATUS_COMPLETED, STATUS_SKIPPED):
        return True
    # Check if we should wait based on status and wait_on_pending flag
    should_wait = False
    if status == STATUS_CLAIMED:
        # Always wait if dependency is running
        should_wait = True
    elif status == STATUS_PENDING and wait_on_pending:
        # Wait if dependency is pending and we're configured to wait on pending
        should_wait = True
    
    if should_wait:
        # Ensure dependency is queued (idempotent)
        enqueue_task(engine, session_id, dep_type)
        # Reset this task to pending
        reset_task_to_pending(engine, my_task_id)
        logger.info(
            "Task %s waiting for %s (status=%s), reset to pending",
            my_task_id, dep_type, status,
        )
        raise TaskDeferred
    
    # Dependency is not blocking (e.g., failed, missing, or pending with wait_on_pending=False)
    # Ensure it's queued so it can run
    enqueue_task(engine, session_id, dep_type)
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


def reset_task_to_pending(engine: Engine, task_id: int) -> None:
    """Reset a claimed task back to pending so the scheduler can re-dispatch it.
    
    Decrements priority by 1 so deferred tasks don't immediately re-claim
    ahead of others, but age-based ordering (TASK_AGE_RATE) ensures they
    eventually catch up.
    """
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                UPDATE demo_tasks
                SET status = :pending, worker_id = NULL, updated_at = NOW(),
                    priority = priority - 1
                WHERE id = :id;
                """
            ),
            {"pending": STATUS_PENDING, "id": task_id},
        )
    signal_dispatch()


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


