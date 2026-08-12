"""Background task queue for demo operations."""

import io
import logging
import tarfile
import uuid
from datetime import datetime, timezone
from typing import Callable

import sqlalchemy as sa
from minio import Minio, S3Error
from sqlalchemy import Engine

from masterbase.lib import demo_blob_name, raw_blob_name

logger = logging.getLogger(__name__)

# Task types
TASK_COMPRESS = "compress"
TASK_BACKUP = "backup"

# Task statuses
STATUS_PENDING = "pending"
STATUS_CLAIMED = "claimed"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


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


def compress_demo(minio_client: Minio, session_id: str) -> str | None:
    """Compress a raw demo from rawblobs to demoblobs.

    Args:
        minio_client: MinIO client
        session_id: Session ID to compress

    Returns:
        Error message or None on success
    """
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


# Registry of task handlers
TASK_HANDLERS: dict[str, Callable[[Minio, str], str | None]] = {
    TASK_COMPRESS: compress_demo,
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
    for task_type in TASK_HANDLERS:
        task = claim_task(engine, task_type, worker_id)
        if task is None:
            continue

        task_id = task["id"]
        session_id = task["session_id"]
        handler = TASK_HANDLERS[task_type]

        logger.info("Processing task %s (%s) for session %s", task_id, task_type, session_id)

        try:
            error = handler(minio_client, session_id)
            if error:
                fail_task(engine, task_id, error)
            else:
                complete_task(engine, task_id)
        except Exception as e:
            logger.error("Task %s raised exception: %s", task_id, e, exc_info=True)
            fail_task(engine, task_id, str(e))

        return True

    return False
