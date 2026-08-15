"""Cleanup task handler - singleton task for periodic storage maintenance."""

from minio import Minio
from sqlalchemy import Engine

from masterbase.cleanup import (
    audit_storage_use,
    cleanup_hung_sessions,
    cleanup_pruned_demos,
    prune_if_necessary,
)
from masterbase.tasks import cleanup_old_tasks
from masterbase.tasks.handlers import TaskHandler

TASK_CLEANUP = "cleanup"


class CleanupTask(TaskHandler):
    """Handler for periodic cleanup tasks.
    
    This is a singleton task - only one instance exists at a time,
    independent of any demo session.
    """
    task_type = TASK_CLEANUP
    singleton = True
    
    @classmethod
    def is_done(cls, minio_client: Minio, engine: Engine, session_id: str) -> bool:
        """Cleanup is never pre-done - it always needs to run."""
        return False
    
    @classmethod
    def run(cls, minio_client: Minio, engine: Engine, session_id: str, task_id: int) -> str | None:
        """Run all cleanup operations."""
        try:
            cleanup_hung_sessions(engine)
            audit_storage_use(engine, minio_client)
            prune_if_necessary(engine, minio_client)
            cleanup_pruned_demos(engine, minio_client)
            cleanup_old_tasks(engine)
        except Exception as e:
            return str(e)
        return None
