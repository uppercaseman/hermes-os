"""A concrete `TaskExecutor` wrapping a `WorkflowEngine`.

The same translation `WorkflowEngineTaskDispatcher`
(workflow_engine/commander_bridge.py) already does, adapted to the
`TaskExecutor` protocol instead of Commander's `TaskDispatcher`, so a
`Worker` -- not Commander directly -- drives execution. This is what
lets a workflow run through the durable queue instead of inline.

Retroactively tags the `QueuedTask` with the `WorkflowRun`'s id once it
exists, completing workflow-level tracking (requirement #14): the run's
id genuinely isn't known until after `start_run()` begins, and Workflow
Engine's own internals are untouched.
"""
from __future__ import annotations

from hermes.modules.task_queue.models import QueuedTask, TaskExecutionResult
from hermes.modules.task_queue.service import TaskQueue
from hermes.modules.workflow_engine.interface import WorkflowEngine


class WorkflowEngineTaskExecutor:
    def __init__(self, *, engine: WorkflowEngine, queue: TaskQueue) -> None:
        self._engine = engine
        self._queue = queue

    async def execute(self, task: QueuedTask) -> TaskExecutionResult:
        workflow_id = task.payload.get("step")
        try:
            run = await self._engine.start_run(workflow_id, input=task.payload, requesting_agent_id="task_queue")
        except Exception as exc:  # noqa: BLE001 -- reported to the queue's retry/dead-letter path, never raised
            return TaskExecutionResult(status="failed", error=str(exc))

        await self._queue.set_workflow_run_id(task.id, run.id)

        if run.status == "completed":
            return TaskExecutionResult(status="completed", output={"run_id": str(run.id)})
        return TaskExecutionResult(status="failed", error=f"workflow run ended with status {run.status!r}")
