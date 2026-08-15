"""Background task queue for demo operations."""

import io
import json
import logging
import math
import os
import shutil
import subprocess
import tarfile
import threading
import time
import uuid
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import Any, Callable, Type

import sqlalchemy as sa
from minio import Minio, S3Error
from sqlalchemy import Engine

from masterbase.lib import demo_blob_name, json_blob_name, raw_blob_name

logger = logging.getLogger(__name__)

# Task types
TASK_COMPRESS = "compress"
TASK_BACKUP = "backup"
TASK_ANALYZE = "analyze"

# Task statuses
STATUS_PENDING = "pending"
STATUS_CLAIMED = "claimed"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# Retention for completed/failed tasks (in days)
TASK_RETENTION_DAYS = 7


class TaskDeferred(Exception):
    """Raised when a task resets itself to pending via depend_on().
    
    The worker catches this and skips complete/fail since the task
    was already reset to pending in the DB.
    """
    pass


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
    if status == STATUS_COMPLETED:
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


# Thread pool size
TASK_WORKER_THREADS = int(os.getenv("TASK_WORKER_THREADS", "4"))

# Analysis config
ANALYSIS_BINARY = os.getenv("ANALYSIS_BINARY")
ANALYSIS_TIMEOUT = int(os.getenv("ANALYSIS_TIMEOUT", "3600"))
MEDIA_DIR = "/media"
ANALYSIS_DIR = os.path.join(MEDIA_DIR, "analysis")


def enqueue_task(engine: Engine, session_id: str, task_type: str, priority: int = 0) -> int:
    """Add a task to the queue.

    Args:
        engine: Database engine
        session_id: Session ID to operate on
        task_type: Type of task (e.g., 'compress', 'backup')
        priority: Higher values = more urgent (default 0)

    Returns:
        Task ID
    """
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
                "session_id": session_id,
                "task_type": task_type,
                "status": STATUS_PENDING,
                "priority": priority,
            },
        )
        task_id = result.scalar_one_or_none()

    if task_id is not None:
        logger.info("Enqueued task %s for session %s (id=%s)", task_type, session_id, task_id)
    else:
        logger.info("Task %s already queued for session %s", task_type, session_id)

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
                    ORDER BY priority DESC, created_at ASC
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


def fail_task(engine: Engine, task_id: int, error: str) -> None:
    """Mark a task as failed with an error message."""
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
    """Reset a claimed task back to pending so the scheduler can re-dispatch it."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                UPDATE demo_tasks
                SET status = :pending, worker_id = NULL, updated_at = NOW()
                WHERE id = :id;
                """
            ),
            {"pending": STATUS_PENDING, "id": task_id},
        )


class TaskHandler:
    """Base class for task handlers.
    
    Subclasses implement is_done() to check if work is already complete
    (by inspecting artifacts in MinIO/DB), and run() for the task logic.
    This avoids duplicating "is this done?" checks across multiple handlers.
    """
    
    task_type: str
    
    @classmethod
    def is_done(cls, minio_client: Minio, engine: Engine, session_id: str) -> bool:
        """Check if this task's work is already done for the given session.
        
        Subclasses should check the actual artifacts (MinIO blobs, DB records)
        rather than the task queue status, as a task may have completed without
        ever being enqueued (e.g., external ingestion, manual operations).
        
        Returns:
            True if the work is already done and the task can be skipped.
        """
        raise NotImplementedError
    
    @classmethod
    def run(cls, minio_client: Minio, engine: Engine, session_id: str, task_id: int) -> str | None:
        """Execute the task.
        
        Args:
            minio_client: MinIO client
            engine: Database engine
            session_id: Session ID to operate on
            task_id: Task ID for dependency management
        
        Returns:
            Error message or None on success. Raises TaskDeferred if waiting on a dependency.
        """
        raise NotImplementedError


class CompressTask(TaskHandler):
    """Handler for compression tasks."""
    
    task_type = TASK_COMPRESS
    
    @classmethod
    def is_done(cls, minio_client: Minio, engine: Engine, session_id: str) -> bool:
        """Check if the compressed demo blob already exists in demoblobs."""
        try:
            minio_client.stat_object("demoblobs", demo_blob_name(session_id))
            return True
        except S3Error:
            return False
    
    @classmethod
    def run(cls, minio_client: Minio, engine: Engine, session_id: str, task_id: int) -> str | None:
        """Execute compression by delegating to compress_demo()."""
        return compress_demo(minio_client, engine, session_id, task_id)


class AnalyzeTask(TaskHandler):
    """Handler for analysis tasks."""
    
    task_type = TASK_ANALYZE
    
    @classmethod
    def is_done(cls, minio_client: Minio, engine: Engine, session_id: str) -> bool:
        """Check if analysis has already been ingested for this session.
        
        Checks the ingested flag in demo_sessions, which is set when analysis
        results are successfully ingested (either internally or via external client).
        """
        with engine.connect() as conn:
            result = conn.execute(
                sa.text(
                    """
                    SELECT ingested FROM demo_sessions
                    WHERE session_id = :session_id;
                    """
                ),
                {"session_id": session_id},
            )
            row = result.fetchone()
            return row.ingested if row else False
    
    @classmethod
    def run(cls, minio_client: Minio, engine: Engine, session_id: str, task_id: int) -> str | None:
        """Execute analysis by delegating to analyze_demo()."""
        return analyze_demo(minio_client, engine, session_id, task_id)


# Task handler registry: maps task type to handler class
TASK_HANDLER_REGISTRY: dict[str, Type[TaskHandler]] = {
    TASK_COMPRESS: CompressTask,
    TASK_ANALYZE: AnalyzeTask,
}


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
                WHERE status IN (:completed, :failed)
                  AND completed_at < NOW() - INTERVAL :interval;
                """
            ),
            {
                "completed": STATUS_COMPLETED,
                "failed": STATUS_FAILED,
                "interval": f"{TASK_RETENTION_DAYS} days",
            },
        )
        count = result.rowcount
        if count:
            logger.info("Cleaned up %d old task records.", count)
    return count


def compress_demo(minio_client: Minio, engine: Engine, session_id: str, task_id: int) -> str | None:
    """Compress a raw demo from rawblobs to demoblobs.

    Args:
        minio_client: MinIO client
        engine: Database engine
        session_id: Session ID to compress
        task_id: Task ID for dependency management

    Returns:
        Error message or None on success
    """
    # Wait for analysis if it's already running (analysis may be using raw demo)
    analysis_status = get_task_status(engine, session_id, TASK_ANALYZE)
    if analysis_status == STATUS_CLAIMED:
        depend_on(engine, session_id, TASK_ANALYZE, task_id, wait_on_pending=False)

    raw_name = raw_blob_name(session_id)
    compressed_name = demo_blob_name(session_id)

    # Read raw demo
    try:
        raw_stat = minio_client.stat_object("rawblobs", raw_name)
        raw_data = minio_client.get_object("rawblobs", raw_name)
        raw_bytes = raw_data.read()
        raw_data.stream().close()
    except S3Error as e:
        if e.code == "NoSuchKey":
            # Raw blob already gone, check if compressed exists
            try:
                minio_client.stat_object("demoblobs", compressed_name)
                logger.info("Session %s: raw blob missing but compressed exists, skipping", session_id)
                return None
            except S3Error:
                return f"Neither raw nor compressed blob found for session {session_id}"
        return f"Failed to read raw blob: {e}"

    # Create tar.xz archive
    tar_buffer = io.BytesIO()
    try:
        with tarfile.open(fileobj=tar_buffer, mode="w:xz") as tar:
            tarinfo = tarfile.TarInfo(name=f"{session_id}.dem")
            tarinfo.size = len(raw_bytes)
            tar.addfile(tarinfo, io.BytesIO(raw_bytes))
    except Exception as e:
        return f"Failed to create tar.xz: {e}"

    tar_buffer.seek(0)
    compressed_size = tar_buffer.getbuffer().nbytes
    original_size = len(raw_bytes)

    # Upload compressed
    try:
        tar_buffer.seek(0)
        minio_client.put_object(
            "demoblobs",
            compressed_name,
            data=tar_buffer,
            length=compressed_size,
            metadata={"original_size": str(original_size)},
        )
    except S3Error as e:
        return f"Failed to upload compressed blob: {e}"

    # Delete raw blob
    try:
        minio_client.remove_object("rawblobs", raw_name)
    except S3Error as e:
        logger.warning("Failed to delete raw blob %s: %s (will be cleaned up later)", raw_name, e)

    logger.info(
        "Compressed demo %s: %d -> %d bytes (%.1f%%)",
        session_id, original_size, compressed_size,
        compressed_size / max(original_size, 1) * 100,
    )
    return None


def analyze_demo(minio_client: Minio, engine: Engine, session_id: str, task_id: int) -> str | None:
    """Download demo, run analysis binary, ingest results.

    Steps:
    1. Check if compressed demo available; if not, depend_on compress
    2. Download demo (from demoblobs if available, else rawblobs)
    3. Write to temp folder
    4. Execute analysis binary via subprocess
    5. Upload analysis JSON to jsonblobs
    6. Ingest results into DB
    7. Cleanup temp folder

    Returns:
        Error message or None on success
    """
    if not ANALYSIS_BINARY:
        return "ANALYSIS_BINARY not configured"

    from masterbase.analysis import submit_analysis as ingest_analysis

    # Check if compressed demo is available
    compressed_available = False
    try:
        minio_client.stat_object("demoblobs", demo_blob_name(session_id))
        compressed_available = True
    except S3Error:
        pass

    if not compressed_available:
        # Compress not done - check if it's running or pending
        # Wait for compression if it's queued (pending or claimed)
        compress_status = get_task_status(engine, session_id, TASK_COMPRESS)
        if compress_status in (STATUS_CLAIMED, STATUS_PENDING):
            depend_on(engine, session_id, TASK_COMPRESS, task_id, wait_on_pending=True)
        # If compress task doesn't exist or failed, fall back to rawblobs

    work_dir = os.path.join(ANALYSIS_DIR, session_id)
    try:
        os.makedirs(work_dir, exist_ok=True)

        # Download demo: prefer compressed (demoblobs), fall back to rawblobs
        demo_path = os.path.join(work_dir, f"{session_id}.dem")
        downloaded = False

        if compressed_available:
            compressed_name = demo_blob_name(session_id)
            try:
                response = minio_client.get_object("demoblobs", compressed_name)
                try:
                    compressed_data = response.read()
                finally:
                    response.close()
                # Decompress tar.xz
                import tarfile as tf_mod
                import io as io_mod
                with tf_mod.open(fileobj=io_mod.BytesIO(compressed_data), mode="r:xz") as tar:
                    member = tar.extractfile(f"{session_id}.dem")
                    if member:
                        with open(demo_path, "wb") as f:
                            shutil.copyfileobj(member, f)
                        downloaded = True
            except S3Error:
                pass

        if not downloaded:
            # Fall back to rawblobs
            raw_name = raw_blob_name(session_id)
            try:
                response = minio_client.get_object("rawblobs", raw_name)
                try:
                    with open(demo_path, "wb") as f:
                        shutil.copyfileobj(response, f)
                finally:
                    response.close()
                downloaded = True
            except S3Error as e:
                return f"Failed to download demo: {e}"

        if not downloaded:
            return "Demo not found in demoblobs or rawblobs"

        # Run analysis binary
        output_path = os.path.join(work_dir, "analysis.json")
        try:
            result = subprocess.run(
                [ANALYSIS_BINARY, "-q", "-i", demo_path, "-o", output_path],
                capture_output=True,
                timeout=ANALYSIS_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            return f"Analysis timed out after {ANALYSIS_TIMEOUT}s"
        except FileNotFoundError:
            return f"Analysis binary not found: {ANALYSIS_BINARY}"

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            return f"Analysis exited with code {result.returncode}: {stderr}"

        # Read analysis JSON
        try:
            with open(output_path, "r") as f:
                analysis_data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            return f"Failed to read analysis output: {e}"

        # Upload to jsonblobs for archival
        json_name = json_blob_name(session_id)
        json_bytes = json.dumps(analysis_data).encode("utf-8")
        try:
            minio_client.put_object(
                "jsonblobs",
                json_name,
                data=io.BytesIO(json_bytes),
                length=len(json_bytes),
            )
        except S3Error as e:
            logger.warning("Failed to upload analysis JSON to MinIO: %s", e)

        # Ingest into DB
        from masterbase.models import Analysis
        try:
            analysis_obj = Analysis(**analysis_data)
        except Exception as e:
            return f"Invalid analysis data: {e}"

        error = ingest_analysis(minio_client, engine, session_id, analysis_obj)
        if error:
            return error

        logger.info("Analyzed demo %s successfully", session_id)
        return None

    finally:
        # Cleanup temp folder
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception as e:
            logger.warning("Failed to cleanup analysis temp dir %s: %s", work_dir, e)


# Global engine reference for handlers that need it (set by cleanup runner)
_engine_ref: Engine | None = None


def set_engine_ref(engine: Engine) -> None:
    """Set the global engine reference for task handlers."""
    global _engine_ref
    _engine_ref = engine


# Registry of task handlers: maps task type to handler class.
# Use handler_cls.run() to execute, handler_cls.is_done() to check completion.
TASK_HANDLERS: dict[str, Type[TaskHandler]] = {
    TASK_COMPRESS: CompressTask,
    TASK_ANALYZE: AnalyzeTask,
}


def process_next_task(engine: Engine, minio_client: Minio, worker_id: str) -> bool:
    """Process the next available task.

    Args:
        engine: Database engine
        minio_client: MinIO client
        worker_id: ID of this worker

    Returns:
        True if a task was processed, False if no tasks available
    """
    # Try each task type in order of priority
    for task_type, handler_cls in TASK_HANDLERS.items():
        task = claim_task(engine, task_type, worker_id)
        if task is None:
            continue

        task_id = task["id"]
        session_id = task["session_id"]

        logger.info("Processing task %s (%s) for session %s", task_id, task_type, session_id)

        try:
            error = handler_cls.run(minio_client, engine, session_id, task_id)
            if error:
                fail_task(engine, task_id, error)
            else:
                complete_task(engine, task_id)
        except TaskDeferred:
            # Task was reset to pending by depend_on(), don't mark complete/fail
            logger.debug("Task %s deferred (dependency not ready)", task_id)
        except Exception as e:
            logger.error("Task %s raised exception: %s", task_id, e, exc_info=True)
            fail_task(engine, task_id, str(e))

        return True

    return False
