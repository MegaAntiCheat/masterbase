"""Cleanup task handler - periodic storage maintenance."""

from minio import Minio
from sqlalchemy import Engine

from masterbase.analysis import release_expired_claims
from masterbase.cleanup import (
    audit_storage_use,
    cleanup_hung_sessions,
    cleanup_pruned_demos,
    prune_if_necessary,
)
from masterbase.tasks.handlers import TaskHandler
from masterbase.tasks import CLAIM_TIMEOUT_MINUTES

TASK_CLEANUP = "cleanup"


class CleanupTask(TaskHandler):
    """Handler for periodic cleanup tasks.

    This is a singleton task - not tied to any demo session.
    """
    task_type = TASK_CLEANUP

    @classmethod
    def run(cls, minio_client: Minio, engine: Engine, session_id: str) -> str | None:
        """Run all cleanup operations."""
        try:
            cleanup_hung_sessions(engine)
            release_expired_claims(engine, CLAIM_TIMEOUT_MINUTES)
            audit_storage_use(engine, minio_client)
            prune_if_necessary(engine, minio_client)
            cleanup_pruned_demos(engine, minio_client)
        except Exception as e:
            return str(e)
        return None
