"""Pydantic data contracts for Mission Control.

These are computed views over the live Mission source. None of
these types are stored -- they are reconstructed on every API
call. That keeps Mission Control stateless from the perspective
of mission data and avoids cache consistency bugs.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# Mirrors the canonical MissionStatus Literal defined in
# hermes.modules.mission_system.models. Mission Control does NOT
# import that type -- the values are repeated here to keep
# Mission Control free of any downward import.
MissionStatusGroup = Literal[
    # Implementation-nicknamed values currently in use
    "draft",
    "team_assigned",
    "active",
    # Canonical 13-state values
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


class MissionSummary(BaseModel):
    """The headline summary of one mission. Computed on demand."""

    mission_id: uuid.UUID
    goal: str
    status: MissionStatusGroup
    owner: str | None = None
    progress_percent: float = 0.0
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class MissionProgress(BaseModel):
    """Aggregated progress rollup: counts of success criteria met
    and the overall percentage."""

    mission_id: uuid.UUID
    total_criteria: int
    criteria_met: int
    criteria_unmet: int
    criteria_pending: int
    progress_percent: float


class MissionTimelineEntry(BaseModel):
    """One entry in a mission timeline, derived from the bus log."""

    event_type: str
    source_module: str
    ts: datetime
    correlation_id: uuid.UUID
    payload: dict[str, Any] = Field(default_factory=dict)


class MissionLogEntry(BaseModel):
    """One log entry, derived from the bus log. Distinct from
    `MissionTimelineEntry` so future logging infrastructure can
    filter on severity / source without polluting the timeline."""

    ts: datetime
    level: str
    message: str
    source_module: str | None = None
    correlation_id: uuid.UUID | None = None


class MissionStatistics(BaseModel):
    """Computed mission-level statistics."""

    total_missions: int
    by_status: dict[str, int] = Field(default_factory=dict)
    success_rate: float = 0.0
    average_duration_seconds: float = 0.0


__all__ = [
    "MissionLogEntry",
    "MissionProgress",
    "MissionStatistics",
    "MissionSummary",
    "MissionTimelineEntry",
    "MissionStatusGroup",
]