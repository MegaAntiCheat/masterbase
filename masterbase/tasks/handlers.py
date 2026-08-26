"""Base class for pipeline task handlers."""

from minio import Minio
from sqlalchemy import Engine


class TaskHandler:
    """Base class for pipeline task handlers.
    
    Each handler represents one stage in the pipeline (compress, analyze, etc.).
    The pipeline order is defined in TASK_ORDER in __init__.py.
    
    Subclasses implement run() for the task logic. The runner checks the
    boolean column in demo_pipeline to determine if work is already done.
    """
    
    task_type: str  # "compress", "analyze", etc.
    
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
