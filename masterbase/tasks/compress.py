"""Compression task handler."""

import io
import logging
import tarfile

from minio import Minio, S3Error
from sqlalchemy import Engine

from masterbase.lib import demo_blob_name, raw_blob_name
from masterbase.tasks.handlers import TaskHandler

TASK_COMPRESS = "compress"

logger = logging.getLogger(__name__)

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
    # Lazy import to avoid circular imports
    from masterbase.tasks import (
        STATUS_CLAIMED, TASK_ANALYZE, depend_on, get_task_status,
    )
    
    # Wait for analysis if it's already running (analysis may be using raw demo)
    analysis_status = get_task_status(engine, session_id, TASK_ANALYZE)
    if analysis_status == STATUS_CLAIMED:
        depend_on(engine, session_id, TASK_ANALYZE, task_id, wait_on_pending=False)

    raw_name = raw_blob_name(session_id)
    compressed_name = demo_blob_name(session_id)

    # Read raw demo
    try:
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
