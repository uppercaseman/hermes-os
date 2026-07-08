"""Test doubles for Commander's collaborator contracts.

These are NOT specialist-agent implementations -- they are fixed-response
stand-ins used only to exercise Commander's own orchestration logic in
isolation, satisfying the Protocols in contracts.py. The real Memory
Manager, Workflow Engine, Tool Manager, Agent Registry, and Task Queue
modules are future work.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from hermes.core.commander.events import TASK_COMPLETED, TASK_FAILED
from hermes.core.commander.models import (
    AgentRequirement,
    ApprovalDecision,
    DispatchedTask,
    IncomingRequest,
    Intent,
    MemoryRequirement,
    Plan,
    ToolRequirement,
    WorkflowPlan,
)
from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event


class FakeIntentClassifier:
    def __init__(self, intent: Intent) -> None:
        self._intent = intent

    async def classify(self, request: IncomingRequest) -> Intent:
        return self._intent


class FailingIntentClassifier:
    """Simulates a collaborator that blows up during planning."""

    async def classify(self, request: IncomingRequest) -> Intent:
        raise RuntimeError("intent classification blew up")


class SlowIntentClassifier:
    """Simulates a collaborator that hangs -- used to test Commander's
    planning-phase timeout guard (see `Commander._with_timeout`)."""

    def __init__(self, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds

    async def classify(self, request: IncomingRequest) -> Intent:
        await asyncio.sleep(self._delay_seconds)
        return Intent(name="never_gets_here", confidence=1.0)


class FakeWorkflowResolver:
    def __init__(self, plan: WorkflowPlan) -> None:
        self._plan = plan

    async def resolve(self, intent: Intent, request: IncomingRequest) -> WorkflowPlan:
        return self._plan


class SlowWorkflowResolver:
    """Simulates a Workflow Engine call that hangs -- proves the planning
    timeout applies to every stage, not just intent classification."""

    def __init__(self, plan: WorkflowPlan, delay_seconds: float) -> None:
        self._plan = plan
        self._delay_seconds = delay_seconds

    async def resolve(self, intent: Intent, request: IncomingRequest) -> WorkflowPlan:
        await asyncio.sleep(self._delay_seconds)
        return self._plan


class FakeAgentResolver:
    def __init__(self, agents: list[AgentRequirement]) -> None:
        self._agents = agents

    async def resolve(self, intent: Intent, workflow: WorkflowPlan) -> list[AgentRequirement]:
        return self._agents


class FakeToolResolver:
    def __init__(self, tools: list[ToolRequirement]) -> None:
        self._tools = tools

    async def resolve(self, intent: Intent, workflow: WorkflowPlan) -> list[ToolRequirement]:
        return self._tools


class FakeMemoryResolver:
    def __init__(self, memory: MemoryRequirement) -> None:
        self._memory = memory

    async def resolve(self, intent: Intent, workflow: WorkflowPlan) -> MemoryRequirement:
        return self._memory


class FakeApprovalPolicy:
    def __init__(self, decision: ApprovalDecision) -> None:
        self._decision = decision

    async def evaluate(self, plan: Plan) -> ApprovalDecision:
        return self._decision


class RecordingTaskDispatcher:
    """Records dispatched tasks but never completes them -- used to test
    Commander's timeout handling, standing in for a Task Queue whose worker
    never reports back."""

    def __init__(self) -> None:
        self.dispatched: list[DispatchedTask] = []

    async def dispatch(self, task: DispatchedTask) -> None:
        self.dispatched.append(task)


class ScriptedTaskDispatcher:
    """Stands in for the Task Queue plus a worker: as soon as a task is
    dispatched, immediately publishes its scripted outcome (`completed` or
    `failed`) on the bus, consuming outcomes in order across all
    dispatch calls (including retries of the same task).
    """

    def __init__(self, bus: EventBus, outcomes: list[str] | None = None) -> None:
        self._bus = bus
        self._outcomes = list(outcomes) if outcomes is not None else None
        self.dispatched: list[DispatchedTask] = []

    async def dispatch(self, task: DispatchedTask) -> None:
        self.dispatched.append(task)
        outcome = self._outcomes.pop(0) if self._outcomes else "completed"
        payload: dict[str, Any] = {"task_id": str(task.id)}
        if outcome == "completed":
            event_type = TASK_COMPLETED
            payload["output"] = {"echo": task.payload.get("step")}
        else:
            event_type = TASK_FAILED
            payload["error"] = "scripted failure"
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module="fake_task_queue",
                correlation_id=task.correlation_id,
                payload=payload,
            )
        )
