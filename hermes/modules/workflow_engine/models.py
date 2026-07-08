"""Pydantic data contracts for the Workflow Engine.

Step sequencing AND parallel steps come from a single mechanism --
`depends_on` -- rather than two separate ones: steps whose dependencies
are all satisfied in the same "wave" run concurrently; a chain of
single dependencies is just the degenerate sequential case. Conditional
branching is a small, safe, structured condition (no eval/exec) that
inspects a prior step's status or output, which also doubles as a
failure-handling branch mechanism -- see `StepCondition` and the
scheduler in service.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from hermes.core.supervisor.policy import RetryPolicy

StepKind = Literal["tool_call", "memory_read", "memory_write", "approval", "noop"]
StepStatus = Literal["pending", "running", "completed", "failed", "skipped", "pending_approval"]
RunStatus = Literal["running", "awaiting_approval", "completed", "failed"]


class StepCondition(BaseModel):
    """A safe, structured condition -- no eval/exec. Inspects the
    referenced step's status (if `path` is None) or a dotted path into
    its output (if set). `step` must be one of the owning step's own
    `depends_on` entries, enforced at registration time, so the
    referenced step is always guaranteed to have already run."""

    step: str
    path: str | None = None
    equals: Any = None


class StepDefinition(BaseModel):
    name: str
    kind: StepKind
    depends_on: list[str] = Field(default_factory=list)
    condition: StepCondition | None = None
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_seconds: float = Field(default=30.0, gt=0)

    # tool_call -- exactly one of tool_name/capability, enforced at registration
    tool_name: str | None = None
    capability: str | None = None
    operation: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    # memory_read / memory_write -- memory_key supports the same
    # {{input.<path>}} / {{steps.<name>.output.<path>}} templates as
    # parameters/memory_value_template (see service.py's
    # _resolve_memory_key), e.g. "research_brief/{{input.topic}}", so a
    # single generic definition can address a different entry per run.
    memory_scope: str | None = None
    memory_key: str | None = None
    memory_owner_agent_id: str | None = None
    memory_value_template: dict[str, Any] = Field(default_factory=dict)

    # approval
    approval_message: str | None = None


class WorkflowDefinition(BaseModel):
    workflow_id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    steps: list[StepDefinition]


class StepResult(BaseModel):
    name: str
    status: StepStatus = "pending"
    output: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None


class WorkflowRun(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    workflow_id: str
    requesting_agent_id: str = "system"
    input: dict[str, Any] = Field(default_factory=dict)
    status: RunStatus = "running"
    step_results: dict[str, StepResult] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
