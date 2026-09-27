"""Background pipeline runner with worker threads."""

import logging
import os
import threading
import time

import sqlalchemy as sa
from minio import Minio
from sqlalchemy import Engine

from masterbase.tasks import (
    TASK_HANDLERS,
    TASK_CLEANUP,
    TASK_WORKER_THREADS,
    CLEANUP_INTERVAL,
    _wait_or_timeout,
    signal_dispatch,
    get_work_item,
    mark_stage_done,
    mark_stage_error,
)

logger = logging.getLogger(__name__)

# Advisory lock key used to elect exactly one task runner across all uvicorn
# worker processes. "MBRU" in hex.
RUNNER_LOCK_KEY = 0x4D425255

# Dedicated connection that holds the advisory lock for this process's
# lifetime. Postgres releases the lock automatically if the connection drops
# (process crash), so a crashed leader can never wedge the pipeline.
_lock_conn: Engine | None = None


def acquire_runner_lock(engine: Engine) -> bool:
    """Try to become the task runner for this deployment.

    Uses pg_try_advisory_lock on a dedicated connection that is kept open
    for the life of the process. Returns True if this process now owns the
    runner slot (and should start the TaskRunner).
    """
    global _lock_conn
    if _lock_conn is not None:
        return True
    try:
        conn = engine.connect()
        acquired = conn.execute(
            sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": RUNNER_LOCK_KEY}
        ).scalar()
    except Exception:
        logger.error("Failed to acquire task runner lock", exc_info=True)
        return False
    if not acquired:
        conn.close()
        return False
    _lock_conn = conn
    return True


def release_runner_lock() -> None:
    """Release the advisory lock and close its dedicated connection."""
    global _lock_conn
    if _lock_conn is not None:
        try:
            _lock_conn.execute(
                sa.text("SELECT pg_advisory_unlock(:key)"), {"key": RUNNER_LOCK_KEY}
            )
        except Exception:
            logger.warning("Failed to release task runner lock", exc_info=True)
        finally:
            _lock_conn.close()
            _lock_conn = None


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

    def is_running(self) -> bool:
        """Whether the runner loop thread is active."""
        return self._running

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
        """Spawn worker threads up to TASK_WORKER_THREADS limit.

        The lock must NOT be held while calling _spawn_worker (it takes the
        same non-reentrant lock) or doing DB work — that deadlocks the loop.
        Only this loop thread spawns workers, and the count can only decrease
        between the check and the increment, so no slot is ever double-issued.
        """
        while True:
            with self._lock:
                if self._running_count >= TASK_WORKER_THREADS:
                    return
            work = get_work_item(self.engine)
            if work is None:
                return
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


# ---------------------------------------------------------------------------
# Per-process supervisor: exactly one TaskRunner across all uvicorn workers.
#
# Every worker process runs _supervisor_loop on a daemon thread. Each one
# tries to take a Postgres advisory lock; only the winner starts the actual
# TaskRunner thread. The lock is held on a dedicated connection for the
# process lifetime, so if the leader crashes Postgres releases it and the
# next watchdog cycle in any surviving worker takes over.
# ---------------------------------------------------------------------------

_supervisor_stop = threading.Event()
_runner: TaskRunner | None = None


def get_task_runner() -> TaskRunner | None:
    """Get this process's task runner instance (if elected)."""
    return _runner


def start_task_runner(engine: Engine, minio_client: Minio) -> None:
    """Start the supervisor for this process.

    Safe to call once per process (Litestar startup runs it in every uvicorn
    worker). The process that wins the advisory lock runs the TaskRunner;
    all others just poll and take over if the leader dies.
    """
    global _runner
    if _runner is not None:
        logger.warning("Task runner supervisor already started.")
        return

    _runner = TaskRunner(engine, minio_client)
    _supervisor_stop.clear()
    t = threading.Thread(target=_supervisor_loop, args=(engine, _runner), daemon=True)
    t.start()


def stop_task_runner() -> None:
    """Stop the supervisor and runner for this process."""
    global _runner
    _supervisor_stop.set()
    if _runner is not None:
        _runner.stop()
        _runner = None
    release_runner_lock()


def _supervisor_loop(engine: Engine, runner: TaskRunner) -> None:
    """Race for the advisory lock; run the TaskRunner while we hold it."""
    logger.info("Task runner supervisor started (pid %s)", os.getpid())
    while not _supervisor_stop.is_set():
        if acquire_runner_lock(engine):
            if not runner.is_running():
                logger.info("This process owns the task runner lock.")
                runner.start()
        elif runner.is_running():
            # We were leader but lost the lock (shouldn't normally happen);
            # step down so the new leader can run.
            runner.stop()

        # Re-check every 10s: if we don't hold the lock and no one does
        # (leader crashed), acquire_runner_lock will succeed next cycle.
        _supervisor_stop.wait(10)

    release_runner_lock()
    logger.info("Task runner supervisor stopped (pid %s)", os.getpid())

