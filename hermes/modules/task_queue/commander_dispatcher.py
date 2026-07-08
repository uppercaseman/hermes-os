"""Bridges Commander's `TaskDispatcher` protocol to a real `TaskQueue`.

Unlike Workflow Engine's `WorkflowEngineTaskDispatcher`
(workflow_engine/commander_bridge.py, unmodified, still valid), which
executes a task inline the instant Commander dispatches it, this bridge
ONLY enqueues -- durably, with retry/priority/scheduling/idempotency all
available -- and returns immediately. A `Worker` is what actually
executes the task and reports back to `TaskQueue`, which is what
publishes the `task.completed`/`task.failed` events Commander's
`_dispatch_and_await` is already listening for.

Preserves Commander's task identity exactly: the enqueued task's `id` IS
`DispatchedTask.id`, not a fresh one -- see `TaskQueue.enqueue`'s
docstring for why this, and its idempotent-on-`id` behavior, both
matter.
"""
from __future__ import annotations

from typing import Any

from hermes.modules.task_queue.service import TaskQueue


class TaskQueueDispatcher:
    def __init__(self, *, queue: TaskQueue) -> None:
        self._queue = queue

    async def dispatch(self, task: Any) -> None:
        """`task` is a Commander `DispatchedTask` -- typed loosely
        (`Any`) so this bridge doesn't need to import Commander's models
        just to type-hint it."""
        await self._queue.enqueue(
            id=task.id,
            kind=task.kind,
            payload=task.payload,
            correlation_id=task.correlation_id,
            # Convention, not a hard rule: a caller that wants mission-
            # level tracking (Mission System) sets correlation_id =
            # mission.id on its IncomingRequest; that value survives
            # unmodified through Commander's Plan into
            # DispatchedTask.correlation_id. This bridge treats it as
            # doing double duty for mission grouping. For any OTHER
            # caller, correlation_id is not a mission id, and this field
            # is simply inert -- never wrong, just not meaningful.
            mission_id=task.correlation_id,
        )
