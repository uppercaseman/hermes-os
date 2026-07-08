"""Test doubles satisfying the Task Queue's narrow collaborator
Protocols -- not real WorkflowEngine/StateManager implementations, used
only to exercise TaskQueue/Worker orchestration logic in isolation.
"""
from __future__ import annotations

from hermes.modules.task_queue.models import QueuedTask, TaskExecutionResult


class FakeTaskExecutor:
    """Scripts a sequence of outcomes ("completed"/"failed"/"raise")
    consumed in the order `execute` is called, one per call."""

    def __init__(self, outcomes: list[str] | None = None) -> None:
        self._outcomes = list(outcomes) if outcomes is not None else ["completed"]
        self.executed_tasks: list[QueuedTask] = []

    async def execute(self, task: QueuedTask) -> TaskExecutionResult:
        self.executed_tasks.append(task)
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if outcome == "raise":
            raise RuntimeError("scripted executor failure")
        if outcome == "failed":
            return TaskExecutionResult(status="failed", error="scripted failure")
        return TaskExecutionResult(status="completed", output={"echo": task.payload})


class FakeHeartbeatReporter:
    def __init__(self) -> None:
        self.reports: list[tuple[str, str]] = []

    async def report_heartbeat(self, module_name: str, state: str, *, detail: str | None = None) -> None:
        self.reports.append((module_name, state))
