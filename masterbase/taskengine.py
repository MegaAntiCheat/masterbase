"""Background pipeline runner with worker threads."""

import logging
import os
import threading
import time

from minio import Minio
from sqlalchemy import Engine

from masterbase.tasks import (
    TASK_HANDLERS,
    TASK_CLEANUP,
    TASK_WORKER_THREADS,
    CLEANUP_INTERVAL,
    _wait_or_timeout,
    signal_dispatch,
    add_to_pipeline,
    get_work_item,
    mark_stage_done,
    mark_stage_error,
)

logger = logging.getLogger(__name__)


class TaskRunner:
    """Background runner that processes the demo pipeline using worker threads."""

    def __init__(self, engine: Engine, minio_client: Minio):
        self.engine = engine
        self.minio_client = minio_client
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        # Track running worker count
        self._running_count = 0
        self._lock = threading.Lock()

    def start(self):
        """Start the background runner thread."""
        if self._running:
            logger.warning("Task runner already started.")
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Pipeline task runner started (%d worker threads).", TASK_WORKER_THREADS)

    def stop(self):
        """Stop the background runner thread."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=CLEANUP_INTERVAL + 10)
            if self._thread.is_alive():
                logger.warning("Task runner thread did not stop gracefully.")
        logger.info("Pipeline task runner stopped.")

    def _run_loop(self):
        """Main loop: schedule cleanup periodically, spawn workers."""
        next_cleanup_time = time.time() + CLEANUP_INTERVAL

        while self._running and not self._stop_event.is_set():
            try:
                now = time.time()
                if now >= next_cleanup_time:
                    self._spawn_worker("__cleanup__", TASK_CLEANUP)
                    next_cleanup_time = time.time() + CLEANUP_INTERVAL

                # Spawn workers for pipeline work
                self._spawn_workers()
            except Exception:
                logger.error("Error in task runner loop", exc_info=True)

            _wait_or_timeout(5)

    def _spawn_workers(self) -> None:
        """Spawn worker threads up to TASK_WORKER_THREADS limit."""
        with self._lock:
            if self._running_count >= TASK_WORKER_THREADS:
                return

            # Try to get work items and spawn workers
            for _ in range(TASK_WORKER_THREADS - self._running_count):
                work = get_work_item(self.engine)
                if work is None:
                    break
                session_id, stage = work
                self._spawn_worker(session_id, stage)

    def _spawn_worker(self, session_id: str, stage: str) -> None:
        """Spawn a worker thread for a pipeline stage."""
        with self._lock:
            self._running_count += 1
        t = threading.Thread(
            target=self._worker, args=(session_id, stage), daemon=True
        )
        t.start()

    def _worker(self, session_id: str, stage: str) -> None:
        """Worker thread: execute a pipeline stage."""
        if session_id == "__cleanup__":
            self._run_cleanup()
            return

        handler = TASK_HANDLERS.get(stage)
        if handler is None:
            logger.error("Unknown stage: %s", stage)
            self._done()
            return

        logger.info("Processing stage %s for session %s", stage, session_id)
        try:
            error = handler.run(self.minio_client, self.engine, session_id)
            if error:
                mark_stage_error(self.engine, session_id, stage, error)
            else:
                mark_stage_done(self.engine, session_id, stage)
                # Signal so the runner can pick up the next stage for this session
                signal_dispatch()
        except Exception as e:
            logger.error("Stage %s for session %s raised: %s", stage, session_id, e, exc_info=True)
            mark_stage_error(self.engine, session_id, stage, str(e))
        finally:
            self._done()

    def _run_cleanup(self) -> None:
        """Run the cleanup task."""
        handler = TASK_HANDLERS[TASK_CLEANUP]
        logger.info("Running periodic cleanup")
        try:
            # Cleanup uses a sentinel session_id
            error = handler.run(self.minio_client, self.engine, "__cleanup__")
            if error:
                logger.warning("Cleanup failed: %s", error)
        except Exception as e:
            logger.error("Cleanup raised: %s", e, exc_info=True)
        finally:
            self._done()

    def _done(self) -> None:
        """Mark a worker as done."""
        with self._lock:
            self._running_count = max(0, self._running_count - 1)


# Module-level singleton for easy access
_runner: TaskRunner | None = None


def get_task_runner() -> TaskRunner | None:
    """Get the current task runner instance."""
    return _runner


def init_task_runner(engine: Engine, minio_client: Minio) -> TaskRunner:
    """Initialize and return the task runner (without starting it)."""
    global _runner
    _runner = TaskRunner(engine, minio_client)
    return _runner


def start_task_runner() -> None:
    """Start the task runner if it exists."""
    if _runner is not None:
        _runner.start()


def stop_task_runner() -> None:
    """Stop the task runner if it exists."""
    global _runner
    if _runner is not None:
        _runner.stop()
        _runner = None
