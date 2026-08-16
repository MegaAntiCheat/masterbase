"""Background task runner with periodic cleanup scheduling."""

import logging
import math
import os
import threading
import time

from minio import Minio
from sqlalchemy import Engine

import sqlalchemy as sa
from masterbase.tasks import (
    TASK_HANDLERS,
    _wait_or_timeout,
    cleanup_old_tasks,
    claim_task,
    complete_task,
    enqueue_task,
    fail_task,
    signal_dispatch,
    skip_task,
    wait_for_tasks,
    TASK_CLEANUP,
    TASK_WORKER_THREADS,
)

logger = logging.getLogger(__name__)

# Interval between cleanup cycles (seconds) - 15 minutes
CLEANUP_INTERVAL: int = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "900"))


class TaskRunner:
    """Background runner that dispatches tasks to worker threads."""

    def __init__(self, engine: Engine, minio_client: Minio):
        self.engine = engine
        self.minio_client = minio_client
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        # Track running tasks per type
        self._running_counts: dict[str, int] = {}
        self._running_lock = threading.Lock()

    def start(self):
        """Start the background task runner thread."""
        if self._running:
            logger.warning("Task runner already started.")
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Background task runner started (%d worker threads).", TASK_WORKER_THREADS)

    def stop(self):
        """Stop the background task runner thread."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=CLEANUP_INTERVAL + 10)
            if self._thread.is_alive():
                logger.warning("Task runner thread did not stop gracefully.")
        logger.info("Background task runner stopped.")

    def _run_loop(self):
        """Main loop: dispatch tasks, schedule cleanup periodically."""
        # Schedule initial cleanup
        next_cleanup_time = time.time() + CLEANUP_INTERVAL

        while self._running and not self._stop_event.is_set():
            try:
                now = time.time()
                if now >= next_cleanup_time:
                    # Enqueue cleanup as a task instead of running inline
                    enqueue_task(self.engine, None, TASK_CLEANUP)
                    next_cleanup_time = time.time() + CLEANUP_INTERVAL

                self._dispatch_tasks()
            except Exception:
                logger.error("Error in task runner loop", exc_info=True)

            # Wait for dispatch signal or 5s fallback for edge cases
            _wait_or_timeout(5)

    def _dispatch_tasks(self) -> None:
        """Dispatch tasks to worker threads using weighted scheduling."""
        with self._running_lock:
            running = sum(self._running_counts.values())
            available = TASK_WORKER_THREADS - running
            if available <= 0:
                return

            # Count pending tasks per type
            pending_counts: dict[str, int] = {}
            for task_type in TASK_HANDLERS:
                with self.engine.connect() as conn:
                    result = conn.execute(
                        sa.text(
                            "SELECT COUNT(*) FROM demo_tasks WHERE status = :status AND task_type = :type"
                        ),
                        {"status": "pending", "type": task_type},
                    )
                    count = result.scalar()
                    if count > 0:
                        pending_counts[task_type] = count

            if not pending_counts:
                return

            dispatched = 0
            while available > 0 and pending_counts:
                # First pass: give each type with no running task one thread
                started = False
                for tt in list(pending_counts):
                    if self._running_counts.get(tt, 0) == 0 and available > 0:
                        self._start_task(tt)
                        pending_counts[tt] -= 1
                        if pending_counts[tt] == 0:
                            del pending_counts[tt]
                        available -= 1
                        dispatched += 1
                        started = True
                if started:
                    continue

                # Second pass: weight by ln(waiting) - running
                best_type = None
                best_score = -float("inf")
                for tt, waiting in pending_counts.items():
                    r = self._running_counts.get(tt, 0)
                    score = math.log(waiting) - r
                    if score > best_score:
                        best_score = score
                        best_type = tt
                if best_type is None:
                    break
                self._start_task(best_type)
                pending_counts[best_type] -= 1
                if pending_counts[best_type] == 0:
                    del pending_counts[best_type]
                available -= 1
                dispatched += 1

            if dispatched:
                logger.debug("Dispatched %d tasks (%d threads available).", dispatched, TASK_WORKER_THREADS)

    def _start_task(self, task_type: str) -> None:
        """Start a worker thread for a task of the given type."""
        self._running_counts[task_type] = self._running_counts.get(task_type, 0) + 1
        worker_id = f"worker-{task_type}-{id(threading.current_thread())}"
        t = threading.Thread(
            target=self._worker, args=(task_type, worker_id), daemon=True
        )
        t.start()

    def _worker(self, task_type: str, worker_id: str) -> None:
        """Worker thread: claim and process a single task."""
        task = claim_task(self.engine, task_type, worker_id)
        if task is None:
            # Task was already claimed by another worker
            with self._running_lock:
                self._running_counts[task_type] = max(0, self._running_counts.get(task_type, 0) - 1)
            return

        task_id = task["id"]
        session_id = task["session_id"]
        handler = TASK_HANDLERS[task_type]

        # Check if work is already done before executing
        if handler.is_done(self.minio_client, self.engine, session_id):
            skip_task(self.engine, task_id)
            return

        # Check dependencies (wait_for) before executing
        if not wait_for_tasks(self.minio_client, self.engine, session_id, handler.wait_for):
            # Dependencies not ready - reset to pending so the scheduler
            # can re-claim this task later once dependencies are satisfied.
            logger.debug(
                "Task %s (%s) deferred: dependencies not ready for session %s",
                task_id, task_type, session_id,
            )
            _reset_to_pending(self.engine, task_id)
            return

        logger.info("Processing task %s (%s) for session %s", task_id, task_type, session_id)
        failed = False
        try:
            error = handler.run(self.minio_client, self.engine, session_id)
            if error:
                fail_task(self.engine, task_id, error)
                failed = True
            else:
                complete_task(self.engine, task_id)
        except Exception as e:
            logger.error("Task %s raised exception: %s", task_id, e, exc_info=True)
            fail_task(self.engine, task_id, str(e))
            failed = True
        finally:
            with self._running_lock:
                self._running_counts[task_type] = max(0, self._running_counts.get(task_type, 0) - 1)
            # Signal dispatch so the scheduler knows a thread slot is free
            # Don't signal on failure to avoid tight retry loops
            if not failed:
                signal_dispatch()


def _reset_to_pending(engine: Engine, task_id: int) -> None:
    """Reset a claimed task back to pending so the scheduler can re-dispatch it.

    Decrements priority by 1 so deferred tasks don't immediately re-claim
    ahead of others, but age-based ordering ensures they eventually catch up.
    """
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                UPDATE demo_tasks
                SET status = :pending, worker_id = NULL, updated_at = NOW(),
                    priority = priority - 1
                WHERE id = :id;
                """
            ),
            {"pending": "pending", "id": task_id},
        )


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
