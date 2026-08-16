"""Base class for task handlers."""

from typing import ClassVar

from minio import Minio
from sqlalchemy import Engine


class TaskHandler:
    """Base class for task handlers.
    
    Subclasses implement is_done() to check if work is already complete
    (by inspecting artifacts in MinIO/DB), and run() for the task logic.
    This avoids duplicating "is this done?" checks across multiple handlers.
    
    Uses wait_for to declare which task types must complete before this one
    activates for a given session_id.
    """
    
    task_type: str
    singleton: bool = False  # If True, task is global (not per-demo); uses sentinel session_id
    
    # List of task type strings that must complete before this task type
    # activates for a given session_id. Checked against completed tasks in
    # the task table first, then actual artifact checks. Skipped task rows
    # are created for caching future dependency checks.
    wait_for: ClassVar[list[str]] = []
    
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
    def run(cls, minio_client: Minio, engine: Engine, session_id: str) -> str | None:
        """Execute the task.
        
        Args:
            minio_client: MinIO client
            engine: Database engine
            session_id: Session ID to operate on
        
        Returns:
            Error message or None on success.
        """
        raise NotImplementedError
