"""Worker -- claims tasks from a TaskQueue and executes them via a
pluggable TaskExecutor.

Task Queue knows nothing about what a task DOES; Worker is the loop
that connects "there's a task" to "something ran it," then reports the
outcome back to the queue (which is what actually publishes
`task.completed`/`task.failed`). Optionally reports busy/idle heartbeats
to State Manager, giving a worker's liveness the same visibility every
other module's has.
"""
from __future__ import annotations

import asyncio

from hermes.modules.task_queue.contracts import HeartbeatReporter, TaskExecutor
from hermes.modules.task_queue.service import TaskQueue


class Worker:
    def __init__(
        self,
        *,
        worker_id: str,
        queue: TaskQueue,
        executor: TaskExecutor,
        poll_interval_seconds: float = 0.5,
        state_manager: HeartbeatReporter | None = None,
    ) -> None:
        self._worker_id = worker_id
        self._queue = queue
        self._executor = executor
        self._poll_interval = poll_interval_seconds
        self._state_manager = state_manager
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        """Starts the background claim/execute loop. Safe to call more
        than once -- a no-op if already running."""
        if self._task is None:
            self._stopping = False
            self._task = asyncio.ensure_future(self._run_loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def run_once(self) -> bool:
        """Claims and executes at most one task. Returns `True` if a
        task was claimed (regardless of outcome), `False` if nothing was
        eligible. Useful for tests, or for driving a worker
        deterministically instead of via the background loop."""
        task = await self._queue.claim_next(self._worker_id)
        if task is None:
            if self._state_manager is not None:
                await self._state_manager.report_heartbeat(self._worker_id, "idle")
            return False

        if self._state_manager is not None:
            await self._state_manager.report_heartbeat(self._worker_id, "busy")

        try:
            result = await self._executor.execute(task)
        except Exception as exc:  # noqa: BLE001 -- an executor's own failure is
            # exactly what fail() + the task's retry policy exists to handle
            await self._queue.fail(task.id, error=str(exc))
            return True

        if result.status == "completed":
            await self._queue.complete(task.id, output=result.output)
        else:
            await self._queue.fail(task.id, error=result.error or "task execution failed")
        return True

    async def _run_loop(self) -> None:
        try:
            while not self._stopping:
                claimed = await self.run_once()
                if not claimed:
                    await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            return
