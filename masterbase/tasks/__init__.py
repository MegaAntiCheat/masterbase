"""Pipeline-based task system for demo operations."""

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

# Event to signal the runner to dispatch work
_dispatch_event = threading.Event()

def signal_dispatch() -> None:
    """Signal the runner to check for new work."""
    _dispatch_event.set()

def _wait_or_timeout(timeout: float) -> None:
    """Wait for the dispatch signal or timeout. Clears the signal after waking."""
    _dispatch_event.wait(timeout=timeout)
    _dispatch_event.clear()

# Thread pool size
TASK_WORKER_THREADS = int(os.getenv("TASK_WORKER_THREADS", "4"))

# Cleanup interval (seconds) - 15 minutes
CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "900"))

# Pipeline stages in order. Each stage maps to a boolean column in demo_pipeline.
# The runner processes sessions by finding the first stage that is False.
TASK_ORDER = [TASK_COMPRESS, TASK_ANALYZE]

# Task handler registry: maps task type to handler class
TASK_HANDLERS: dict[str, type[TaskHandler]] = {
    TASK_COMPRESS: CompressTask,
    TASK_ANALYZE: AnalyzeTask,
    TASK_CLEANUP: CleanupTask,
}

# Analysis config
ANALYSIS_BINARY = "/analysis-binary"
ANALYSIS_TIMEOUT = int(os.getenv("ANALYSIS_TIMEOUT", "3600"))
MEDIA_DIR = "/media"
ANALYSIS_DIR = os.path.join(MEDIA_DIR, "analysis")


# ---------------------------------------------------------------------------
# Pipeline table operations
# ---------------------------------------------------------------------------

PipelineState = dict[str, bool]


def ensure_pipeline_row(engine: Engine, session_id: str) -> PipelineState:
    """Get the pipeline row for a session, creating it if it doesn't exist.
    
    Returns dict mapping task names to completion status, e.g.
    {"compress": False, "analyze": True}.
    """
    with engine.begin() as conn:
        # Ensure row exists
        conn.execute(
            sa.text(
                """
                INSERT INTO demo_pipeline (session_id)
                VALUES (:session_id)
                ON CONFLICT (session_id) DO NOTHING;
                """
            ),
            {"session_id": session_id},
        )
        # Fetch current state
        result = conn.execute(
            sa.text(
                "SELECT compressed, analyzed FROM demo_pipeline WHERE session_id = :sid;"
            ),
            {"sid": session_id},
        )
        row = result.fetchone()
        if row is None:
            return {TASK_COMPRESS: False, TASK_ANALYZE: False}
        return {TASK_COMPRESS: row.compressed, TASK_ANALYZE: row.analyzed}


def add_to_pipeline(engine: Engine, session_id: str) -> None:
    """Add a session to the pipeline (if not already present).
    
    Creates a row with compressed=false, analyzed=false.
    """
    ensure_pipeline_row(engine, session_id)
    logger.info("Added session %s to pipeline", session_id)


def get_next_stage(engine: Engine, session_id: str) -> str | None:
    """Get the next pipeline stage for a session.
    
    Returns the first stage in TASK_ORDER that is not yet True,
    or None if all stages are complete.
    """
    with engine.connect() as conn:
        result = conn.execute(
            sa.text(
                "SELECT compressed, analyzed FROM demo_pipeline WHERE session_id = :sid;"
            ),
            {"sid": session_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        
        compressed, analyzed = row
        for i, stage in enumerate(TASK_ORDER):
            if stage == TASK_COMPRESS and not compressed:
                return stage
            if stage == TASK_ANALYZE and not analyzed:
                return stage
    return None


def get_work_item(engine: Engine) -> tuple[str, str] | None:
    """Get the next session+stage to work on.
    
    Uses FOR UPDATE SKIP LOCKED to allow parallel workers without conflicts.
    Returns (session_id, stage) or None if no work available.
    """
    # Find sessions with pending work, oldest first
    with engine.begin() as conn:
        result = conn.execute(
            sa.text(
                """
                SELECT session_id, compressed, analyzed FROM demo_pipeline
                WHERE compressed = false OR analyzed = false
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED;
                """
            ),
        )
        row = result.fetchone()
        if row is None:
            return None
        
        session_id = row.session_id
        compressed, analyzed = row.compressed, row.analyzed
        
        # Determine next stage based on TASK_ORDER
        for stage in TASK_ORDER:
            if stage == TASK_COMPRESS and not compressed:
                return (session_id, stage)
            if stage == TASK_ANALYZE and not analyzed:
                return (session_id, stage)
    
    return None


def mark_stage_done(engine: Engine, session_id: str, stage: str) -> None:
    """Mark a pipeline stage as complete."""
    col = stage  # "compressed" or "analyzed"
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                f"""
                UPDATE demo_pipeline
                SET {col} = true, error_message = NULL, updated_at = NOW()
                WHERE session_id = :sid;
                """
            ),
            {"sid": session_id},
        )
    logger.info("Stage %s completed for session %s", stage, session_id)


def mark_stage_error(engine: Engine, session_id: str, stage: str, error: str) -> None:
    """Mark a stage as failed with an error message (doesn't block pipeline)."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                UPDATE demo_pipeline
                SET error_message = :error, updated_at = NOW()
                WHERE session_id = :sid;
                """
            ),
            {"sid": session_id, "error": error},
        )
    logger.warning("Stage %s failed for session %s: %s", stage, session_id, error)


def get_pending_sessions(engine: Engine, limit: int = 100) -> list[str]:
    """Get sessions that have incomplete pipeline stages.
    
    Returns session_ids where there's at least one stage that is False.
    Ordered by created_at ASC (oldest first).
    """
    # Build WHERE clause: at least one stage is false
    conditions = []
    for stage in TASK_ORDER:
        col = stage  # "compressed" or "analyzed"
        conditions.append(f"{col} = false")
    where = " OR ".join(conditions)
    
    with engine.connect() as conn:
        result = conn.execute(
            sa.text(
                f"""
                SELECT session_id FROM demo_pipeline
                WHERE {where}
                ORDER BY created_at ASC
                LIMIT :limit;
                """
            ),
            {"limit": limit},
        )
        return [row.session_id for row in result]


def is_pipeline_done(engine: Engine, session_id: str) -> bool:
    """Check if all pipeline stages are complete for a session."""
    with engine.connect() as conn:
        result = conn.execute(
            sa.text(
                """
                SELECT compressed, analyzed FROM demo_pipeline
                WHERE session_id = :sid;
                """
            ),
            {"sid": session_id},
        )
        row = result.fetchone()
        if row is None:
            return False
        return row.compressed and row.analyzed
