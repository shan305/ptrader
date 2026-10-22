"""Background workers with explicit, awaited startup and shutdown.

A worker runs a synchronous job in a thread at a fixed interval. Stopping
sets a flag and waits for the job in progress to finish, so database work is
never cut off half-way and the engine is only disposed once every worker has
stopped. Failed runs are logged and retried with exponential backoff.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class WorkerStatus:
    name: str
    state: str  # created, running, stopped, failed
    runs: int
    failures: int
    consecutive_failures: int
    last_error: str | None
    last_success: float | None


class BackgroundWorker:
    def __init__(self, name: str, job: Callable[[], None], interval: float, max_backoff: float = 30.0):
        self.name = name
        self._job = job
        self.interval = interval
        self.max_backoff = max(max_backoff, interval)
        self._task: asyncio.Task | None = None
        self._stopping: asyncio.Event | None = None
        self._state = "created"
        self._runs = 0
        self._failures = 0
        self._consecutive_failures = 0
        self._last_error: str | None = None
        self._last_success: float | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> WorkerStatus:
        return WorkerStatus(
            self.name,
            self._state,
            self._runs,
            self._failures,
            self._consecutive_failures,
            self._last_error,
            self._last_success,
        )

    async def start(self) -> None:
        if self.running:
            raise RuntimeError(f"Worker {self.name} is already running")
        self._stopping = asyncio.Event()
        self._state = "running"
        self._task = asyncio.create_task(self._loop(), name=f"worker:{self.name}")

    async def stop(self, timeout: float) -> bool:
        """Ask the worker to stop and wait for its current run; returns False if it had to be cancelled."""
        if self._task is None:
            return True
        self._stopping.set()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)
            clean = True
        except TimeoutError:
            logger.error("Worker %s did not stop within %.1fs; cancelling", self.name, timeout)
            self._task.cancel()
            clean = False
        except Exception:
            clean = False
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None
        self._state = "stopped" if clean else "failed"
        return clean

    def _next_delay(self) -> float:
        if self._consecutive_failures == 0:
            return self.interval
        return min(self.interval * 2**self._consecutive_failures, self.max_backoff)

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            self._runs += 1
            try:
                await asyncio.to_thread(self._job)
            except Exception as exc:
                self._failures += 1
                self._consecutive_failures += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("Worker %s run failed", self.name)
            else:
                self._consecutive_failures = 0
                self._last_success = time.time()
            try:
                await asyncio.wait_for(self._stopping.wait(), self._next_delay())
            except TimeoutError:
                pass


class WorkerManager:
    def __init__(self, stop_timeout: float = 5.0):
        self.stop_timeout = stop_timeout
        self._workers: list[BackgroundWorker] = []
        self._started: list[BackgroundWorker] = []

    def add(self, worker: BackgroundWorker) -> None:
        if any(w.name == worker.name for w in self._workers):
            raise ValueError(f"Duplicate worker name {worker.name}")
        self._workers.append(worker)

    @property
    def workers(self) -> list[BackgroundWorker]:
        return list(self._workers)

    async def start_all(self) -> None:
        """Start every worker; if one fails to start, stop the ones already started and re-raise."""
        for worker in self._workers:
            try:
                await worker.start()
            except BaseException:
                await self.stop_all()
                raise
            self._started.append(worker)

    async def stop_all(self) -> None:
        """Stop started workers in reverse order. Never raises."""
        while self._started:
            worker = self._started.pop()
            try:
                await worker.stop(self.stop_timeout)
            except Exception:
                logger.exception("Error while stopping worker %s", worker.name)

    def status(self) -> list[WorkerStatus]:
        return [w.status() for w in self._workers]
