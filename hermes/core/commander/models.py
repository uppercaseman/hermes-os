"""Pydantic data contracts for Hermes Commander.

These are the types that flow across the event bus and between Commander
and its collaborator modules (Memory Manager, Workflow Engine, Tool
Manager, Agent Registry, Task Queue, Configuration Manager) -- none of
which are implemented yet. Commander is built and tested entirely against
these types and the Protocols in contracts.py, so the real modules can be
dropped in later without touching Commander's code.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

TaskStatus = Literal["queued", "claimed", "completed", "failed", "dead_letter"]
ResponseStatus = Literal["completed", "failed", "awaiting_approval"]
MemoryScope = Literal["session", "persistent", "shared"]


class IncomingRequest(BaseModel):
    """Everything Commander receives at its single point of entry."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    correlation_id: uuid.UUID | None = None
    raw_input: str
    requester: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Intent(BaseModel):
    name: str
    confidence: float = Field(ge=0.0, le=1.0)
    slots: dict[str, Any] = Field(default_factory=dict)


class WorkflowPlan(BaseModel):
    workflow_id: str
    name: str
    steps: list[str] = Field(default_factory=list)


class AgentRequirement(BaseModel):
    agent_name: str
    role: str


class ToolRequirement(BaseModel):
    tool_name: str
    reason: str


class MemoryRequirement(BaseModel):
    scope: MemoryScope
    keys: list[str] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    required: bool
    approved: bool = False
    reason: str | None = None
    approver: str | None = None


class DispatchedTask(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    correlation_id: uuid.UUID
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: TaskStatus = "queued"
    attempts: int = 0
    max_attempts: int = 3


class TaskResult(BaseModel):
    task_id: uuid.UUID
    status: Literal["completed", "failed"]
    output: dict[str, Any] | None = None
    error: str | None = None


class Plan(BaseModel):
    """Everything Commander decided about how to satisfy one request."""

    request_id: uuid.UUID
    correlation_id: uuid.UUID
    intent: Intent
    workflow: WorkflowPlan
    agents: list[AgentRequirement]
    tools: list[ToolRequirement]
    memory: MemoryRequirement
    approval: ApprovalDecision | None = None

    def build_tasks(self) -> list[DispatchedTask]:
        """Coarse-grained task breakdown: one task per workflow step.

        This is deliberately dumb -- it does not resolve step ordering or
        dependencies between steps. Dependency-aware, ordered execution
        belongs to the future Workflow Engine module. Commander's job is
        only to hand each step to the Task Queue and track it through to
        completion or failure; if a workflow has no explicit steps, the
        whole workflow is dispatched as a single task.
        """
        steps = self.workflow.steps or [self.workflow.name]
        return [
            DispatchedTask(
                correlation_id=self.correlation_id,
                kind="workflow_step",
                payload={
                    "workflow_id": self.workflow.workflow_id,
                    "step": step,
                    "agents": [a.model_dump() for a in self.agents],
                    "tools": [t.model_dump() for t in self.tools],
                    "memory": self.memory.model_dump(),
                },
            )
            for step in steps
        ]


class StructuredResponse(BaseModel):
    """The one thing Commander hands back to the caller (CLI/HTTP), no
    matter what happened inside."""

    request_id: uuid.UUID
    correlation_id: uuid.UUID
    status: ResponseStatus
    plan: Plan | None
    task_results: list[TaskResult] = Field(default_factory=list)
    summary: str
