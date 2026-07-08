"""Protocol contracts for every collaborator Commander depends on.

None of these modules are implemented yet (Memory Manager, Workflow
Engine, Tool Manager, Agent Registry, Task Queue, Configuration Manager --
see the architecture doc). Commander is written and tested entirely
against these Protocols so that dropping in the real modules later
requires no change to Commander itself -- only a class that satisfies the
relevant protocol.
"""
from __future__ import annotations

from typing import Protocol

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


class IntentClassifier(Protocol):
    """Determines what the requester wants. Backed by a model call in the
    full system -- out of scope here, Commander only consumes the result."""

    async def classify(self, request: IncomingRequest) -> Intent: ...


class WorkflowResolver(Protocol):
    """Owned by the Workflow Engine module."""

    async def resolve(self, intent: Intent, request: IncomingRequest) -> WorkflowPlan: ...


class AgentResolver(Protocol):
    """Owned by the Agent Registry module."""

    async def resolve(self, intent: Intent, workflow: WorkflowPlan) -> list[AgentRequirement]: ...


class ToolResolver(Protocol):
    """Owned by the Tool Manager module."""

    async def resolve(self, intent: Intent, workflow: WorkflowPlan) -> list[ToolRequirement]: ...


class MemoryResolver(Protocol):
    """Owned by the Memory Manager module."""

    async def resolve(self, intent: Intent, workflow: WorkflowPlan) -> MemoryRequirement: ...


class ApprovalPolicy(Protocol):
    """Decides whether a plan needs human sign-off before dispatch, and
    whether it already has it (e.g. a pre-approved automation)."""

    async def evaluate(self, plan: Plan) -> ApprovalDecision: ...


class TaskDispatcher(Protocol):
    """Owned by the Task Queue module. Enqueues a task; does not return its
    result inline -- completion/failure comes back asynchronously as a
    `task.completed` / `task.failed` event on the bus."""

    async def dispatch(self, task: DispatchedTask) -> None: ...
