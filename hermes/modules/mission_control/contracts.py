"""Mission Control Protocol contracts.

Defines the single downward edge in the workspace layer:
`MissionSource`. The real Mission System satisfies this Protocol
implicitly; tests pass an in-memory fake.

`MissionControlProtocol` is the read-only consumer surface the
future UI binds against.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from hermes.core.event_bus.models import Event
from hermes.modules.mission_control.models import (
    MissionLogEntry,
    MissionProgress,
    MissionStatistics,
    MissionSummary,
    MissionTimelineEntry,
)


@runtime_checkable
class MissionLike(Protocol):
    """The structural shape of one mission Mission Control reads from
    the source. The real Mission System's `Mission` class satisfies
    this implicitly; tests pass a Pydantic model with these fields."""

    id: uuid.UUID
    goal: str
    status: str
    assigned_team: list
    success_criteria: list
    outputs: dict
    created_at: datetime
    updated_at: datetime


@runtime_checkable
class MissionSource(Protocol):
    """Narrow read-only surface Mission Control depends on.

    The real Mission System satisfies this Protocol implicitly --
    its concrete methods include `list_missions()` and
    `get_mission()`. Tests pass an in-memory fake."""

    def list_missions(self) -> list[MissionLike]:
        ...

    def get_mission(self, mission_id: uuid.UUID) -> MissionLike | None:
        ...


@runtime_checkable
class MissionControlProtocol(Protocol):
    def list_running_missions(self) -> list[MissionSummary]:
        ...

    def list_queued_missions(self) -> list[MissionSummary]:
        ...

    def list_ready_missions(self) -> list[MissionSummary]:
        ...

    def list_paused_missions(self) -> list[MissionSummary]:
        ...

    def list_waiting_missions(self) -> list[MissionSummary]:
        ...

    def list_blocked_missions(self) -> list[MissionSummary]:
        ...

    def list_completed_missions(self) -> list[MissionSummary]:
        ...

    def list_failed_missions(self) -> list[MissionSummary]:
        ...

    def list_cancelled_missions(self) -> list[MissionSummary]:
        ...

    def list_archived_missions(self) -> list[MissionSummary]:
        ...

    async def mission_summary(
        self, mission_id: uuid.UUID
    ) -> MissionSummary | None:
        ...

    async def mission_progress(
        self, mission_id: uuid.UUID
    ) -> MissionProgress | None:
        ...

    def mission_timeline(
        self, mission_id: uuid.UUID
    ) -> list[MissionTimelineEntry]:
        ...

    def mission_logs(
        self, mission_id: uuid.UUID
    ) -> list[MissionLogEntry]:
        ...

    def mission_ownership(self, mission_id: uuid.UUID) -> dict[str, Any]:
        ...

    def statistics(self) -> MissionStatistics:
        ...

    async def live_event_stream(self) -> AsyncIterator[Event]:
        ...


__all__ = [
    "MissionControlProtocol",
    "MissionLike",
    "MissionSource",
]