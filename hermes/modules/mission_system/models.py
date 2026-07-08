"""Pydantic data contracts for the Mission System."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

# ADR-0017 reconciliation: the canonical 13-state mission lifecycle (see
# ADR 0014 + Mission Lifecycle spec) names its states as `created`,
# `planned`, `awaiting_approval`, `ready`, `running`, `paused`, `waiting`,
# `blocked`, `completed`, `failed`, `cancelled`, `dissolved`, `archived`.
#
# The runtime today uses seven of those (and three implementation-nicknamed
# values: `draft` for the pre-team-build entry state, `team_assigned`
# between `assign_team()` and `execute_mission()`, and `active` for the
# post-execution-start state). Per ADR 0017 (Sprint 0), we ACCEPT ALL 13
# canonical values in this Literal, so a future migration to the canonical
# vocabulary does not require changing this type. Runtime code continues to
# read/write the seven values it always has; new code may choose any of the
# 13 canonical names. See mission_system/README.md and ADR 0017 for the
# alias mapping.
MissionStatus = Literal[
    # Implementation-nicknamed values currently in use (kept valid for backward compatibility)
    "draft",            # pre-team-build entry state (canonical alias: created)
    "team_assigned",    # post-assign_team(), pre-execute_mission() (no canonical equivalent; treated as a sub-state of planned)
    "active",           # post-execute_mission() start (canonical alias: running)
    # Canonical 13-state values from ADR 0014 / Mission Lifecycle spec
    "created",
    "planned",
    "awaiting_approval",
    "ready",
    "running",
    "paused",
    "waiting",
    "blocked",
    "completed",
    "failed",
    "cancelled",
    "dissolved",
    "archived",
]


class SuccessCriterion(BaseModel):
    """`met` is `None` until explicitly judged via
    `MissionSystem.mark_success_criterion` -- whether a criterion is
    satisfied is never evaluated automatically here (that would need
    real judgment, which this framework deliberately doesn't build)."""

    description: str
    met: bool | None = None


class ApprovalRecord(BaseModel):
    gate_name: str
    approved: bool = False
    approver: str | None = None
    decided_at: datetime | None = None


class SpecialistRole(BaseModel):
    """A TEMPORARY role assigned to one mission -- not a specialist
    agent. No AI/model logic is attached: this is a scoped permission
    record (which capabilities/tools/memory scopes this role may touch)
    that a future real agent implementation would consult before acting."""

    role_name: str
    mission_id: uuid.UUID
    agent_id: str
    required_capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    memory_scopes: list[str] = Field(default_factory=list)
    status: Literal["active", "dissolved"] = "active"

    def can_use_capability(self, capability: str) -> bool:
        return capability in self.required_capabilities

    def can_use_tool(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools

    def can_access_memory_scope(self, scope: str) -> bool:
        return scope in self.memory_scopes


class Mission(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    goal: str
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_memory_scopes: list[str] = Field(default_factory=list)
    required_workflows: list[str] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    requested_roles: list[str] = Field(
        default_factory=list, description="Explicit role names, bypassing capability-based inference if set."
    )

    status: MissionStatus = "draft"
    assigned_team: list[SpecialistRole] = Field(default_factory=list)
    approvals_granted: dict[str, ApprovalRecord] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
