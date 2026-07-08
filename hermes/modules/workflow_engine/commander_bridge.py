"""Bridges Commander's `TaskDispatcher` protocol to a `WorkflowEngine`.

This is the ONLY file in this module that knows about Commander's types
-- the rest of Workflow Engine's API is entirely in its own vocabulary.
Nothing in Commander changes to make this work:

Commander's `Plan.build_tasks()` (core/commander/models.py) already
falls back to dispatching exactly ONE task, keyed by the workflow's
`name`, whenever a `WorkflowPlan.steps` list is empty. A `WorkflowResolver`
that wants Workflow Engine to own execution just needs to return
`WorkflowPlan(steps=[], name=<workflow_id>, ...)` -- Commander then
dispatches one opaque task per plan instead of one per step, exactly as
Commander's own docstring anticipated ("Commander's job here is only to
hand each step to the Task Queue... dependency-aware execution belongs
to the future Workflow Engine module").

This class is what receives that task: it reads `task.payload["step"]`
(which holds the workflow's name in this single-task case) as the
workflow_id to run, executes the ENTIRE workflow -- with its own
sequencing, branching, parallelism, retries, and approval gates, none of
which Commander ever sees -- and reports back with `task.completed` /
`task.failed` carrying the matching `task_id`, so Commander's existing,
unmodified `_dispatch_and_await` resolves normally.
"""
from __future__ import annotations

from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.workflow_engine.service import WorkflowEngine

TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"

SOURCE_MODULE = "workflow_engine"


class WorkflowEngineTaskDispatcher:
    """Satisfies Commander's `TaskDispatcher` protocol
    (core/commander/contracts.py) by running the dispatched task's
    workflow to completion via a `WorkflowEngine`."""

    def __init__(self, *, engine: WorkflowEngine, event_bus: EventBus) -> None:
        self._engine = engine
        self._bus = event_bus

    async def dispatch(self, task: Any) -> None:
        """`task` is a Commander `DispatchedTask`, typed loosely (`Any`)
        so importing Commander's models isn't required just to type-hint
        this bridge -- it only ever reads `.payload`, `.correlation_id`,
        and `.id`."""
        workflow_id = task.payload.get("step")
        try:
            run = await self._engine.start_run(
                workflow_id, input=task.payload, requesting_agent_id="commander"
            )
        except Exception as exc:  # noqa: BLE001 -- reported to Commander via the bus, never raised back
            await self._publish(TASK_FAILED, task, {"error": str(exc)})
            return

        if run.status == "completed":
            await self._publish(TASK_COMPLETED, task, {"output": {"run_id": str(run.id)}})
        else:
            await self._publish(TASK_FAILED, task, {"error": f"workflow run ended with status {run.status!r}"})

    async def _publish(self, event_type: str, task: Any, payload: dict[str, Any]) -> None:
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module=SOURCE_MODULE,
                correlation_id=task.correlation_id,
                payload={"task_id": str(task.id), **payload},
            )
        )
