"""Mission Control service.

The single downward-edge consumer in the workspace layer: it
reads from a `MissionSource` Protocol (the real Mission System
satisfies it implicitly; tests pass an in-memory fake) and exposes
a read-only aggregated view of the world to the future desktop UI.

Key properties:

- **No cache.** Every list / summary / progress / statistics call
  walks the live source. The cost is one dict lookup per call;
  the benefit is that we never serve stale data.
- **No mutation.** Mission Control is read-only; it never asks
  the source to mutate a mission, never writes to memory, never
  invokes the workflow engine. It only reads.
- **Bus-aware timeline reconstruction.** When an event bus is
  provided, Mission Control subscribes to `"*"` and maintains a
  bounded ring of recent events. `mission_timeline(mission_id)`
  filters this ring by `correlation_id == mission_id` -- so the
  timeline is a derived view, not stored state.
- **Live event stream.** `live_event_stream()` is an async
  generator that subscribes to the bus and yields each event as
  it arrives. When no bus is provided, the iterator yields nothing.
"""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Deque, Optional

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.mission_control import events as evt
from hermes.modules.mission_control.contracts import MissionSource
from hermes.modules.mission_control.errors import UnknownMissionError
from hermes.modules.mission_control.models import (
    MissionLogEntry,
    MissionProgress,
    MissionStatistics,
    MissionSummary,
    MissionTimelineEntry,
)

SOURCE_MODULE = "mission_control"


# Status -> group. The "queued" group has no canonical match -- it's
# the pre-team-build state.
_STATUS_GROUPS: dict[str, str] = {
    # Implementation-nicknamed values
    "draft": "queued",
    "team_assigned": "queued",
    # Canonical 13-state values
    "created": "queued",
    "planned": "queued",
    "awaiting_approval": "queued",
    "ready": "queued",
    "running": "running",
    "active": "running",
    "paused": "paused",
    "waiting": "waiting",
    "blocked": "blocked",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "dissolved": "cancelled",
    "archived": "archived",
}


def _group_for(status: str) -> str:
    return _STATUS_GROUPS.get(status, "queued")


def _is_terminal(status: str) -> bool:
    return status in {"completed", "failed", "cancelled", "dissolved", "archived"}


def _owner_for(mission: Any) -> Optional[str]:
    """Best-effort mission owner extraction. The real Mission class
    does not have a single owner field; the closest is the
    `requested_roles[0]` or `assigned_team[0].agent_id`."""
    team = getattr(mission, "assigned_team", None) or []
    if team and hasattr(team[0], "agent_id"):
        return str(team[0].agent_id)
    return None


def _progress_percent(mission: Any) -> float:
    """Derived progress percentage from `success_criteria`. Falls
    back to 0.0 when no criteria are present."""
    criteria = getattr(mission, "success_criteria", None) or []
    if not criteria:
        return 0.0
    met = sum(1 for c in criteria if getattr(c, "met", None) is True)
    return round(100.0 * met / len(criteria), 2)


class MissionControl:
    def __init__(
        self,
        *,
        mission_source: MissionSource,
        event_bus: EventBus | None = None,
        recent_event_buffer_size: int = 1024,
        clock=None,
    ) -> None:
        if recent_event_buffer_size < 1:
            raise ValueError("recent_event_buffer_size must be >= 1")
        self._source = mission_source
        self._bus = event_bus
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._events: Deque[Event] = deque(maxlen=recent_event_buffer_size)
        self._subscribed = False
        self._streams: set[uuid.UUID] = set()
        if self._bus is not None:
            # Subscribe lazily in start(); here we just track state.
            self._pending_subscription = True
        else:
            self._pending_subscription = False

    # ------------------------------------------------------------------ #
    # Bus integration
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        """Subscribes to the bus's wildcard if one was provided.
        Idempotent."""
        if self._subscribed or self._bus is None:
            self._subscribed = self._bus is not None
            self._pending_subscription = False
            return
        await self._bus.subscribe("*", self._on_event)
        self._subscribed = True
        self._pending_subscription = False

    async def stop(self) -> None:
        if not self._subscribed or self._bus is None:
            return
        await self._bus.unsubscribe("*", self._on_event)
        self._subscribed = False

    async def _on_event(self, event: Event) -> None:
        self._events.append(event)

    # ------------------------------------------------------------------ #
    # Group listings
    # ------------------------------------------------------------------ #
    def _by_group(self, group: str) -> list[MissionSummary]:
        result: list[MissionSummary] = []
        for mission in self._source.list_missions():
            if _group_for(mission.status) != group:
                continue
            result.append(self._summarize(mission))
        return result

    def list_running_missions(self) -> list[MissionSummary]:
        return self._by_group("running")

    def list_queued_missions(self) -> list[MissionSummary]:
        return self._by_group("queued")

    def list_ready_missions(self) -> list[MissionSummary]:
        # "ready" is its own canonical state; filter directly.
        return [
            self._summarize(m)
            for m in self._source.list_missions()
            if m.status == "ready"
        ]

    def list_paused_missions(self) -> list[MissionSummary]:
        return self._by_group("paused")

    def list_waiting_missions(self) -> list[MissionSummary]:
        return self._by_group("waiting")

    def list_blocked_missions(self) -> list[MissionSummary]:
        return self._by_group("blocked")

    def list_completed_missions(self) -> list[MissionSummary]:
        return self._by_group("completed")

    def list_failed_missions(self) -> list[MissionSummary]:
        return self._by_group("failed")

    def list_cancelled_missions(self) -> list[MissionSummary]:
        return self._by_group("cancelled")

    def list_archived_missions(self) -> list[MissionSummary]:
        return self._by_group("archived")

    def list_all_missions(self) -> list[MissionSummary]:
        return [self._summarize(m) for m in self._source.list_missions()]

    # ------------------------------------------------------------------ #
    # Single-mission views
    # ------------------------------------------------------------------ #
    def _summarize(self, mission: Any) -> MissionSummary:
        return MissionSummary(
            mission_id=mission.id,
            goal=mission.goal,
            status=mission.status,
            owner=_owner_for(mission),
            progress_percent=_progress_percent(mission),
            started_at=getattr(mission, "created_at", None),
            updated_at=getattr(mission, "updated_at", None),
            completed_at=(
                getattr(mission, "updated_at", None)
                if _is_terminal(mission.status)
                else None
            ),
        )

    async def mission_summary(
        self, mission_id: uuid.UUID
    ) -> MissionSummary | None:
        mission = self._source.get_mission(mission_id)
        if mission is None:
            return None
        summary = self._summarize(mission)
        await self._publish(
            evt.MISSION_SUMMARY_VIEWED,
            {
                "mission_id": str(mission_id),
                "status": summary.status,
            },
        )
        return summary

    async def mission_progress(
        self, mission_id: uuid.UUID
    ) -> MissionProgress | None:
        mission = self._source.get_mission(mission_id)
        if mission is None:
            return None
        criteria = getattr(mission, "success_criteria", None) or []
        total = len(criteria)
        met = sum(1 for c in criteria if getattr(c, "met", None) is True)
        unmet = sum(1 for c in criteria if getattr(c, "met", None) is False)
        pending = total - met - unmet
        pct = (
            round(100.0 * met / total, 2) if total > 0 else 0.0
        )
        return MissionProgress(
            mission_id=mission_id,
            total_criteria=total,
            criteria_met=met,
            criteria_unmet=unmet,
            criteria_pending=pending,
            progress_percent=pct,
        )

    def mission_timeline(
        self, mission_id: uuid.UUID
    ) -> list[MissionTimelineEntry]:
        """Returns the bus-log timeline for `mission_id`. Each
        entry is a bus event whose `correlation_id == mission_id`."""
        entries: list[MissionTimelineEntry] = []
        for event in self._events:
            if event.correlation_id == mission_id:
                entries.append(
                    MissionTimelineEntry(
                        event_type=event.event_type,
                        source_module=event.source_module,
                        ts=event.ts,
                        correlation_id=event.correlation_id,
                        payload=event.payload,
                    )
                )
        return entries

    def mission_logs(
        self, mission_id: uuid.UUID
    ) -> list[MissionLogEntry]:
        """Returns the log entries for `mission_id` derived from the
        bus log. Distinct from the timeline so future logging
        infrastructure can filter on severity / source."""
        out: list[MissionLogEntry] = []
        for event in self._events:
            if event.correlation_id != mission_id:
                continue
            message = event.payload.get("message") or event.event_type
            out.append(
                MissionLogEntry(
                    ts=event.ts,
                    level=event.level,
                    message=str(message),
                    source_module=event.source_module,
                    correlation_id=event.correlation_id,
                )
            )
        return out

    def mission_ownership(self, mission_id: uuid.UUID) -> dict[str, Any]:
        """Returns ownership details for `mission_id`: the
        assigned team members (with role name + agent id) and the
        `requested_roles`."""
        mission = self._source.get_mission(mission_id)
        if mission is None:
            raise UnknownMissionError(mission_id)
        team = getattr(mission, "assigned_team", None) or []
        return {
            "mission_id": str(mission_id),
            "assigned_team": [
                {
                    "role_name": getattr(t, "role_name", None),
                    "agent_id": getattr(t, "agent_id", None),
                    "status": getattr(t, "status", None),
                }
                for t in team
            ],
            "requested_roles": list(
                getattr(mission, "requested_roles", None) or []
            ),
        }

    # ------------------------------------------------------------------ #
    # Statistics
    # ------------------------------------------------------------------ #
    def statistics(self) -> MissionStatistics:
        missions = self._source.list_missions()
        by_status: dict[str, int] = defaultdict(int)
        terminal_count = 0
        completed_count = 0
        total_duration_seconds = 0.0
        for m in missions:
            by_status[m.status] += 1
            if _is_terminal(m.status):
                terminal_count += 1
                if m.status == "completed":
                    completed_count += 1
                started = getattr(m, "created_at", None)
                ended = getattr(m, "updated_at", None)
                if (
                    isinstance(started, datetime)
                    and isinstance(ended, datetime)
                    and ended > started
                ):
                    total_duration_seconds += (
                        ended - started
                    ).total_seconds()
        avg_duration = (
            total_duration_seconds / terminal_count
            if terminal_count > 0
            else 0.0
        )
        success_rate = (
            completed_count / terminal_count if terminal_count > 0 else 0.0
        )
        return MissionStatistics(
            total_missions=len(missions),
            by_status=dict(by_status),
            success_rate=round(success_rate, 4),
            average_duration_seconds=round(avg_duration, 2),
        )

    # ------------------------------------------------------------------ #
    # Live event stream
    # ------------------------------------------------------------------ #
    async def live_event_stream(self) -> AsyncIterator[Event]:
        """Yields bus events as they arrive. Caller may break out
        of the loop; the subscription is torn down on generator close."""
        if self._bus is None:
            return
        subscription_id = uuid.uuid4()
        self._streams.add(subscription_id)
        queue: asyncio.Queue[Optional[Event]] = asyncio.Queue()

        async def handler(event: Event) -> None:
            await queue.put(event)

        await self._bus.subscribe("*", handler)
        await self._publish(
            evt.MISSION_STREAM_SUBSCRIBED,
            {"subscription_id": str(subscription_id)},
        )
        try:
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                yield ev
        finally:
            await self._bus.unsubscribe("*", handler)
            self._streams.discard(subscription_id)
            await self._publish(
                evt.MISSION_STREAM_UNSUBSCRIBED,
                {"subscription_id": str(subscription_id)},
            )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module=SOURCE_MODULE,
                correlation_id=uuid.uuid4(),
                payload=payload,
            )
        )


__all__ = ["MissionControl"]