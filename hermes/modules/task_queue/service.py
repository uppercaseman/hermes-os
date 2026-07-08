"""TaskQueue -- durable execution, retries, crash recovery, and mission/
workflow continuity for dispatched work.

Task Queue knows nothing about what a task DOES (that's `TaskExecutor`'s
job, via a `Worker`) or who submitted it (Commander, Mission System, or
anything else) -- it only owns: persisting a task, deciding what's
eligible to be claimed next (priority, schedule, dependencies), applying
`RetryPolicy` on failure (the sixth reuse of that one building block
across this codebase), moving exhausted tasks to the dead-letter queue,
and recovering claims from workers that never reported back.

Two identity guarantees this class exists specifically to uphold, both
required for `commander_dispatcher.py`'s bridge to work at all:

1. `enqueue(id=...)` lets a caller supply the task's id explicitly
   (Commander's own `DispatchedTask.id`) instead of generating a fresh
   one -- Commander's `_dispatch_and_await` matches completion events by
   that exact id, so the queue must never mint a different one for the
   same logical task.
2. `enqueue()` is idempotent on that same `id`: calling it again for an
   id that already exists returns the EXISTING task unchanged. This
   matters because Commander's own task-level retry can call
   `dispatcher.dispatch(task)` again for the identical `DispatchedTask`
   (same id) if a completion event doesn't arrive in time -- without
   this, a second enqueue would silently reset an already-in-flight
   task's state.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.core.supervisor.policy import RetryPolicy
from hermes.modules.task_queue import events as evt
from hermes.modules.task_queue.backends import InMemoryTaskBackend
from hermes.modules.task_queue.contracts import TaskStorageBackend
from hermes.modules.task_queue.errors import InvalidTaskStateError, UnknownTaskError
from hermes.modules.task_queue.models import QueuedTask

SOURCE_MODULE = "task_queue"


class TaskQueue:
    def __init__(
        self,
        *,
        backend: TaskStorageBackend | None = None,
        event_bus: EventBus | None = None,
        visibility_timeout_seconds: float = 60.0,
        max_claim_attempts: int = 3,
    ) -> None:
        self._backend = backend or InMemoryTaskBackend()
        self._bus = event_bus
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._max_claim_attempts = max_claim_attempts
        self._idempotency_index: dict[str, uuid.UUID] = {}

    # ------------------------------------------------------------------ #
    # Creating / persisting tasks
    # ------------------------------------------------------------------ #
    async def enqueue(
        self,
        *,
        id: uuid.UUID | None = None,
        kind: str = "generic",
        payload: dict[str, Any] | None = None,
        priority: int = 100,
        scheduled_for: datetime | None = None,
        depends_on: list[uuid.UUID] | None = None,
        idempotency_key: str | None = None,
        mission_id: uuid.UUID | None = None,
        workflow_run_id: uuid.UUID | None = None,
        correlation_id: uuid.UUID | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> QueuedTask:
        if id is not None:
            existing_by_id = await self._backend.get(id)
            if existing_by_id is not None:
                return existing_by_id

        if idempotency_key is not None and idempotency_key in self._idempotency_index:
            existing = await self._backend.get(self._idempotency_index[idempotency_key])
            if existing is not None:
                return existing

        task = QueuedTask(
            id=id or uuid.uuid4(),
            kind=kind,
            payload=payload or {},
            priority=priority,
            scheduled_for=scheduled_for,
            depends_on=depends_on or [],
            idempotency_key=idempotency_key,
            mission_id=mission_id,
            workflow_run_id=workflow_run_id,
            correlation_id=correlation_id or uuid.uuid4(),
            retry_policy=retry_policy or RetryPolicy(),
        )
        await self._backend.save(task)
        if idempotency_key is not None:
            self._idempotency_index[idempotency_key] = task.id
        await self._publish(evt.TASK_ENQUEUED, task, {})
        return task

    # ------------------------------------------------------------------ #
    # Claiming / completing / failing
    # ------------------------------------------------------------------ #
    async def claim_next(self, worker_id: str) -> QueuedTask | None:
        """Returns the highest-priority eligible task (scheduled time
        reached, dependencies completed, currently `queued`), or `None`
        if nothing is eligible. Sets a visibility timeout so a worker
        that dies mid-task doesn't hold it forever -- see
        `recover_expired_claims`."""
        now = datetime.now(timezone.utc)
        candidates = []
        for task in await self._backend.list_all():
            if task.status != "queued":
                continue
            if task.scheduled_for is not None and task.scheduled_for > now:
                continue
            if not await self._dependencies_satisfied(task):
                continue
            candidates.append(task)
        if not candidates:
            return None

        candidates.sort(key=lambda t: (t.priority, t.created_at))
        chosen = candidates[0]
        chosen.status = "claimed"
        chosen.claimed_by = worker_id
        chosen.claimed_at = now
        chosen.visible_at = now + timedelta(seconds=self._visibility_timeout_seconds)
        chosen.updated_at = now
        await self._backend.save(chosen)
        await self._publish(evt.TASK_CLAIMED, chosen, {"worker_id": worker_id})
        return chosen

    async def _dependencies_satisfied(self, task: QueuedTask) -> bool:
        """A dependency that's missing or dead-lettered can never
        complete -- rather than block `task` forever, this cascades the
        failure onto it immediately (a lazy check, run each time
        `claim_next` considers the task, not a separate periodic sweep)."""
        for dep_id in task.depends_on:
            dep = await self._backend.get(dep_id)
            if dep is None or dep.status == "dead_letter":
                if task.status != "dead_letter":
                    task.status = "dead_letter"
                    task.error = f"dependency {dep_id} is unavailable or dead-lettered"
                    task.updated_at = datetime.now(timezone.utc)
                    await self._backend.save(task)
                    await self._publish(evt.TASK_DEAD_LETTERED, task, {"reason": "unsatisfiable dependency"})
                return False
            if dep.status != "completed":
                return False
        return True

    async def complete(self, task_id: uuid.UUID, *, output: dict[str, Any] | None = None) -> QueuedTask:
        task = await self._require(task_id)
        if task.status != "claimed":
            raise InvalidTaskStateError(f"task {task_id} is not claimed (status={task.status!r})")
        task.status = "completed"
        task.output = output or {}
        task.attempts += 1
        task.updated_at = datetime.now(timezone.utc)
        await self._backend.save(task)
        await self._publish(evt.TASK_COMPLETED, task, {"output": task.output})
        return task

    async def fail(self, task_id: uuid.UUID, *, error: str) -> QueuedTask:
        """Applies the task's own `RetryPolicy`: re-queues (with backoff
        as a `scheduled_for` delay) if attempts remain, otherwise moves
        to the dead-letter queue and publishes `task.failed` -- the
        event Commander is actually listening for."""
        task = await self._require(task_id)
        if task.status != "claimed":
            raise InvalidTaskStateError(f"task {task_id} is not claimed (status={task.status!r})")

        attempt = task.attempts + 1
        task.attempts = attempt
        task.error = error
        task.claimed_by = None
        task.claimed_at = None
        task.visible_at = None

        if task.retry_policy.should_retry(attempt, task.retry_policy.max_attempts):
            backoff = task.retry_policy.next_backoff(attempt)
            task.status = "queued"
            task.scheduled_for = datetime.now(timezone.utc) + timedelta(seconds=backoff) if backoff > 0 else None
            task.updated_at = datetime.now(timezone.utc)
            await self._backend.save(task)
            await self._publish(evt.TASK_RETRY_SCHEDULED, task, {"attempt": attempt, "backoff_seconds": backoff})
            return task

        task.status = "dead_letter"
        task.updated_at = datetime.now(timezone.utc)
        await self._backend.save(task)
        await self._publish(evt.TASK_DEAD_LETTERED, task, {"attempt": attempt, "error": error})
        await self._publish(evt.TASK_FAILED, task, {"error": error})
        return task

    # ------------------------------------------------------------------ #
    # Crash recovery
    # ------------------------------------------------------------------ #
    async def recover_expired_claims(self) -> int:
        """A claimed task whose visibility timeout has passed without
        being completed/failed is presumed to belong to a dead worker.
        It's requeued, bounded by `max_claim_attempts` (distinct from
        the task's own retry-policy attempt count) -- exhausting THAT
        bound dead-letters it too, so a worker that keeps crashing on
        the same task can't hold it forever."""
        now = datetime.now(timezone.utc)
        recovered = 0
        for task in await self._backend.list_all():
            if task.status != "claimed" or task.visible_at is None or task.visible_at > now:
                continue
            task.claim_attempts += 1
            task.claimed_by = None
            task.claimed_at = None
            task.visible_at = None
            task.updated_at = now
            if task.claim_attempts >= self._max_claim_attempts:
                task.status = "dead_letter"
                task.error = "worker never reported completion (visibility timeout exceeded)"
                await self._backend.save(task)
                await self._publish(evt.TASK_DEAD_LETTERED, task, {"reason": "claim_attempts_exhausted"})
                await self._publish(evt.TASK_FAILED, task, {"error": task.error})
            else:
                task.status = "queued"
                await self._backend.save(task)
                await self._publish(evt.TASK_RECOVERED, task, {"claim_attempts": task.claim_attempts})
            recovered += 1
        return recovered

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    async def get_task(self, task_id: uuid.UUID) -> QueuedTask:
        return await self._require(task_id)

    async def list_tasks_for_mission(self, mission_id: uuid.UUID) -> list[QueuedTask]:
        return [t for t in await self._backend.list_all() if t.mission_id == mission_id]

    async def list_tasks_for_workflow_run(self, workflow_run_id: uuid.UUID) -> list[QueuedTask]:
        return [t for t in await self._backend.list_all() if t.workflow_run_id == workflow_run_id]

    async def list_dead_letter_tasks(self) -> list[QueuedTask]:
        return [t for t in await self._backend.list_all() if t.status == "dead_letter"]

    async def set_workflow_run_id(self, task_id: uuid.UUID, workflow_run_id: uuid.UUID) -> QueuedTask:
        """Retroactively tags a task with the workflow run it ended up
        starting. The run's id isn't known until AFTER execution begins
        (Workflow Engine's own internals are untouched by this module),
        so this is how workflow-level tracking is completed once that id
        exists -- see `workflow_executor.py`."""
        task = await self._require(task_id)
        task.workflow_run_id = workflow_run_id
        task.updated_at = datetime.now(timezone.utc)
        await self._backend.save(task)
        return task

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    async def _require(self, task_id: uuid.UUID) -> QueuedTask:
        task = await self._backend.get(task_id)
        if task is None:
            raise UnknownTaskError(task_id)
        return task

    async def _publish(self, event_type: str, task: QueuedTask, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module=SOURCE_MODULE,
                correlation_id=task.correlation_id,
                payload={"task_id": str(task.id), "status": task.status, **payload},
            )
        )
