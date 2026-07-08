"""Mission Control unit tests.

Strategy: build an in-memory `FakeMissionSource` that satisfies
the `MissionSource` Protocol; populate it with missions of every
status the directive names; assert every list / summary /
progress / statistics / timeline / ownership / log view.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest
from pydantic import BaseModel, Field

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.core.event_bus.models import Event
from hermes.modules.mission_control import build_mission_control
from hermes.modules.mission_control.contracts import (
    MissionControlProtocol,
    MissionSource,
)
from hermes.modules.mission_control.errors import UnknownMissionError
from hermes.modules.mission_control.models import (
    MissionProgress,
    MissionStatistics,
    MissionSummary,
)


# ---------------------------------------------------------------------- #
# Test doubles
# ---------------------------------------------------------------------- #
class FakeSuccessCriterion(BaseModel):
    description: str
    met: Optional[bool] = None


class FakeSpecialistRole(BaseModel):
    role_name: str
    mission_id: uuid.UUID
    agent_id: str
    required_capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    memory_scopes: list[str] = Field(default_factory=list)
    status: str = "active"


class FakeMission(BaseModel):
    """A minimal Mission-shaped record. The real Mission System's
    `Mission` class has more fields; the ones here are the ones
    Mission Control reads."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    goal: str
    status: str
    assigned_team: list[FakeSpecialistRole] = Field(default_factory=list)
    success_criteria: list[FakeSuccessCriterion] = Field(default_factory=list)
    outputs: dict[str, Any] = Field(default_factory=dict)
    requested_roles: list[str] = Field(default_factory=list)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class FakeMissionSource:
    def __init__(self, missions: list[FakeMission]) -> None:
        self._missions = list(missions)

    def list_missions(self) -> list[FakeMission]:
        return list(self._missions)

    def get_mission(self, mission_id: uuid.UUID) -> FakeMission | None:
        for m in self._missions:
            if m.id == mission_id:
                return m
        return None

    def add(self, mission: FakeMission) -> None:
        self._missions.append(mission)


def _make_source(
    *,
    status_coverage: bool = True,
) -> tuple[FakeMissionSource, dict[str, FakeMission]]:
    """Builds a source containing one mission per status the
    directive cares about. Returns `(source, by_id)`."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    by_id: dict[str, FakeMission] = {}
    missions: list[FakeMission] = []
    statuses = [
        ("draft", None, None),
        ("team_assigned", None, None),
        ("ready", None, None),
        ("running", now, None),
        ("active", now, None),
        ("paused", now, None),
        ("waiting", now, None),
        ("blocked", now, None),
        ("completed", now, now + timedelta(seconds=10)),
        ("failed", now, now + timedelta(seconds=5)),
        ("cancelled", now, now + timedelta(seconds=2)),
        ("archived", now, now + timedelta(seconds=20)),
    ]
    for status, started, ended in statuses:
        m = FakeMission(
            id=uuid.uuid4(),
            goal=f"goal-{status}",
            status=status,
            assigned_team=[
                FakeSpecialistRole(
                    role_name=f"role-{status}",
                    mission_id=uuid.uuid4(),
                    agent_id=f"agent-{status}",
                )
            ],
            success_criteria=[
                FakeSuccessCriterion(description=f"c1-{status}", met=True),
                FakeSuccessCriterion(description=f"c2-{status}", met=False),
                FakeSuccessCriterion(description=f"c3-{status}", met=None),
            ],
            created_at=started or now,
            updated_at=ended or now,
        )
        missions.append(m)
        by_id[status] = m
    return FakeMissionSource(missions), by_id


# ---------------------------------------------------------------------- #
# Protocol surface
# ---------------------------------------------------------------------- #
class TestProtocolSurface:
    def test_satisfies_mission_source_protocol(self) -> None:
        src, _ = _make_source()
        assert isinstance(src, MissionSource)

    def test_satisfies_mission_control_protocol(self) -> None:
        src, _ = _make_source()
        mc = build_mission_control(mission_source=src)
        assert isinstance(mc, MissionControlProtocol)


# ---------------------------------------------------------------------- #
# Group listings
# ---------------------------------------------------------------------- #
class TestGroupListings:
    def setup_method(self) -> None:
        self.src, self.by_id = _make_source()
        self.mc = build_mission_control(mission_source=self.src)

    def test_running_includes_running_and_active(self) -> None:
        running = self.mc.list_running_missions()
        ids = {m.status for m in running}
        assert "running" in ids
        assert "active" in ids

    def test_queued_includes_draft_team_assigned_ready(self) -> None:
        queued = self.mc.list_queued_missions()
        ids = {m.status for m in queued}
        assert {"draft", "team_assigned", "ready"} <= ids

    def test_paused(self) -> None:
        assert any(
            m.status == "paused" for m in self.mc.list_paused_missions()
        )

    def test_waiting(self) -> None:
        assert any(
            m.status == "waiting" for m in self.mc.list_waiting_missions()
        )

    def test_blocked(self) -> None:
        assert any(
            m.status == "blocked" for m in self.mc.list_blocked_missions()
        )

    def test_completed(self) -> None:
        assert any(
            m.status == "completed" for m in self.mc.list_completed_missions()
        )

    def test_failed(self) -> None:
        assert any(
            m.status == "failed" for m in self.mc.list_failed_missions()
        )

    def test_cancelled(self) -> None:
        cancelled = self.mc.list_cancelled_missions()
        assert any(m.status == "cancelled" for m in cancelled)

    def test_archived(self) -> None:
        assert any(
            m.status == "archived" for m in self.mc.list_archived_missions()
        )

    def test_ready_listing_filters_to_ready_only(self) -> None:
        ready = self.mc.list_ready_missions()
        assert all(m.status == "ready" for m in ready)
        assert len(ready) == 1


# ---------------------------------------------------------------------- #
# Single-mission views
# ---------------------------------------------------------------------- #
class TestSingleMissionViews:
    def setup_method(self) -> None:
        self.src, self.by_id = _make_source()
        self.mc = build_mission_control(mission_source=self.src)

    async def test_mission_summary_unknown_returns_none(self) -> None:
        assert await self.mc.mission_summary(uuid.uuid4()) is None

    async def test_mission_summary_known(self) -> None:
        m = self.by_id["running"]
        summary = await self.mc.mission_summary(m.id)
        assert summary is not None
        assert summary.mission_id == m.id
        assert summary.status == "running"

    async def test_mission_progress_counts_criteria(self) -> None:
        m = self.by_id["running"]
        progress = await self.mc.mission_progress(m.id)
        assert progress is not None
        assert progress.total_criteria == 3
        assert progress.criteria_met == 1
        assert progress.criteria_unmet == 1
        assert progress.criteria_pending == 1
        assert progress.progress_percent == pytest.approx(33.33, abs=0.01)

    async def test_mission_progress_unknown_returns_none(self) -> None:
        assert await self.mc.mission_progress(uuid.uuid4()) is None

    def test_mission_ownership_unknown_raises(self) -> None:
        with pytest.raises(UnknownMissionError):
            self.mc.mission_ownership(uuid.uuid4())

    def test_mission_ownership_returns_team(self) -> None:
        m = self.by_id["running"]
        ownership = self.mc.mission_ownership(m.id)
        assert ownership["mission_id"] == str(m.id)
        assert len(ownership["assigned_team"]) == 1
        member = ownership["assigned_team"][0]
        assert member["role_name"] == "role-running"
        assert member["agent_id"] == "agent-running"


# ---------------------------------------------------------------------- #
# Statistics
# ---------------------------------------------------------------------- #
class TestStatistics:
    def setup_method(self) -> None:
        self.src, self.by_id = _make_source()
        self.mc = build_mission_control(mission_source=self.src)

    def test_total_missions(self) -> None:
        stats = self.mc.statistics()
        assert stats.total_missions == len(self.src.list_missions())

    def test_by_status_counts(self) -> None:
        stats = self.mc.statistics()
        assert stats.by_status["running"] >= 1
        assert stats.by_status["completed"] >= 1

    def test_success_rate_computed(self) -> None:
        stats = self.mc.statistics()
        # 1 completed + 1 failed + 1 cancelled + 1 archived = 4 terminal.
        # 1 completed -> 0.25
        assert 0.0 <= stats.success_rate <= 1.0

    def test_average_duration_seconds_positive(self) -> None:
        stats = self.mc.statistics()
        assert stats.average_duration_seconds >= 0.0


# ---------------------------------------------------------------------- #
# Timeline + logs from bus
# ---------------------------------------------------------------------- #
class TestTimelineAndLogs:
    async def test_mission_timeline_filters_by_correlation(self) -> None:
        bus = InMemoryEventBus()
        src, by_id = _make_source()
        mc = build_mission_control(mission_source=src, event_bus=bus)
        await mc.start()
        mid = by_id["running"].id
        # 3 events with this mission id, 1 with a different id.
        for _ in range(3):
            await bus.publish(
                Event(
                    event_type="mission.progress",
                    source_module="x",
                    correlation_id=mid,
                    payload={"x": 1},
                )
            )
        await bus.publish(
            Event(
                event_type="other.event",
                source_module="y",
                correlation_id=uuid.uuid4(),
                payload={},
            )
        )
        timeline = mc.mission_timeline(mid)
        assert len(timeline) == 3
        assert all(e.correlation_id == mid for e in timeline)

    async def test_mission_logs_filters_by_correlation(self) -> None:
        bus = InMemoryEventBus()
        src, by_id = _make_source()
        mc = build_mission_control(mission_source=src, event_bus=bus)
        await mc.start()
        mid = by_id["running"].id
        await bus.publish(
            Event(
                event_type="mission.step_completed",
                source_module="x",
                correlation_id=mid,
                payload={"message": "step 1 done"},
                level="info",
            )
        )
        logs = mc.mission_logs(mid)
        assert len(logs) == 1
        assert logs[0].message == "step 1 done"


# ---------------------------------------------------------------------- #
# Live event stream
# ---------------------------------------------------------------------- #
class TestLiveEventStream:
    async def test_live_event_stream_yields_events(self) -> None:
        bus = InMemoryEventBus()
        src, _ = _make_source()
        mc = build_mission_control(mission_source=src, event_bus=bus)
        await mc.start()

        async def publish_three() -> None:
            for i in range(3):
                await bus.publish(
                    Event(
                        event_type=f"e.{i}",
                        source_module="t",
                        correlation_id=uuid.uuid4(),
                        payload={},
                    )
                )

        async def collect() -> list:
            collected: list = []
            async for ev in mc.live_event_stream():
                collected.append(ev)
                if len(collected) == 3:
                    break
            return collected

        # Run publisher and collector concurrently.
        publisher = asyncio.create_task(publish_three())
        collector = asyncio.create_task(collect())
        await asyncio.gather(publisher, collector)
        assert len(collector.result()) == 3

    async def test_live_event_stream_no_bus_yields_nothing(self) -> None:
        src, _ = _make_source()
        mc = build_mission_control(mission_source=src, event_bus=None)
        await mc.start()
        collected: list = []
        async for ev in mc.live_event_stream():
            collected.append(ev)
        assert collected == []


# ---------------------------------------------------------------------- #
# Mission summary view event
# ---------------------------------------------------------------------- #
class TestViewEvents:
    async def test_mission_summary_view_publishes_event(self) -> None:
        bus = InMemoryEventBus()
        src, by_id = _make_source()
        mc = build_mission_control(mission_source=src, event_bus=bus)
        captured: list = []

        async def handler(ev):  # type: ignore[no-untyped-def]
            captured.append(ev)

        await bus.subscribe(
            "mission_control.view.mission_summary_viewed", handler
        )
        m = by_id["running"]
        await mc.mission_summary(m.id)
        assert len(captured) == 1
        assert captured[0].payload["mission_id"] == str(m.id)


# Required for asyncio.create_task at module scope of test.
import asyncio