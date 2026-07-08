"""Session Manager unit tests."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.session_manager import build_session_manager
from hermes.modules.session_manager.contracts import (
    SessionManagerProtocol,
    WorkspaceAccessor,
)
from hermes.modules.session_manager.errors import (
    UnknownSessionError,
    UnknownWorkspaceReferenceError,
)
from hermes.modules.session_manager.models import ActivityKind
from hermes.modules.workspace_manager import build_workspace_manager


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


# ---------------------------------------------------------------------- #
# Lifecycle
# ---------------------------------------------------------------------- #
class TestLifecycle:
    async def test_start_session_returns_record(self) -> None:
        sm = build_session_manager()
        session = await sm.start_session(user_id="alice")
        assert session.user_id == "alice"
        assert session.ended_at is None
        assert session.id is not None

    async def test_start_session_requires_user_id(self) -> None:
        sm = build_session_manager()
        with pytest.raises(ValueError):
            await sm.start_session(user_id="")

    async def test_end_session_unknown_raises(self) -> None:
        sm = build_session_manager()
        with pytest.raises(UnknownSessionError):
            await sm.end_session(uuid.uuid4())

    async def test_end_session_sets_ended_at(self) -> None:
        clock = FakeClock()
        sm = build_session_manager(clock=clock)
        session = await sm.start_session(user_id="u")
        clock.advance(60)
        ended = await sm.end_session(session.id)
        assert ended.ended_at is not None
        assert ended.ended_at == clock.now

    async def test_get_session_returns_copy(self) -> None:
        sm = build_session_manager()
        session = await sm.start_session(user_id="u")
        again = await sm.get_session(session.id)
        assert again is not None
        assert again.id == session.id
        assert again is not session

    async def test_get_session_unknown_returns_none(self) -> None:
        sm = build_session_manager()
        assert await sm.get_session(uuid.uuid4()) is None


# ---------------------------------------------------------------------- #
# Current-X pointers
# ---------------------------------------------------------------------- #
class TestCurrentPointers:
    async def test_set_current_workspace_no_accessor_accepts_any(self) -> None:
        sm = build_session_manager()  # no accessor
        session = await sm.start_session(user_id="u")
        updated = await sm.set_current_workspace(
            session.id, uuid.uuid4()
        )
        assert updated.current_workspace_id is not None

    async def test_set_current_workspace_validates_against_accessor(
        self,
    ) -> None:
        ws = build_workspace_manager()
        sm = build_session_manager(workspace_manager=ws)
        session = await sm.start_session(user_id="u")
        # Unknown workspace id -> raises
        with pytest.raises(UnknownWorkspaceReferenceError):
            await sm.set_current_workspace(session.id, uuid.uuid4())

    async def test_set_current_application(self) -> None:
        sm = build_session_manager()
        session = await sm.start_session(user_id="u")
        updated = await sm.set_current_application(
            session.id, "mission_control"
        )
        assert updated.current_application_id == "mission_control"

    async def test_set_current_mission(self) -> None:
        sm = build_session_manager()
        session = await sm.start_session(user_id="u")
        mid = uuid.uuid4()
        updated = await sm.set_current_mission(session.id, mid)
        assert updated.current_mission_id == mid

    async def test_set_current_project(self) -> None:
        sm = build_session_manager()
        session = await sm.start_session(user_id="u")
        pid = uuid.uuid4()
        updated = await sm.set_current_project(session.id, pid)
        assert updated.current_project_id == pid

    async def test_set_current_application_idempotent_no_event(self) -> None:
        bus = InMemoryEventBus()
        sm = build_session_manager(event_bus=bus)
        session = await sm.start_session(user_id="u")
        events: list = []

        async def handler(ev):  # type: ignore[no-untyped-def]
            events.append(ev)

        await bus.subscribe(
            "session_manager.session.current_application_changed", handler
        )
        await sm.set_current_application(session.id, "mission_control")
        await sm.set_current_application(session.id, "mission_control")
        assert len(events) == 1


# ---------------------------------------------------------------------- #
# Recent activity ring
# ---------------------------------------------------------------------- #
class TestRecentActivity:
    async def test_recent_activity_records_lifecycle(self) -> None:
        sm = build_session_manager()
        session = await sm.start_session(user_id="u")
        await sm.set_current_application(session.id, "mission_control")
        await sm.set_current_mission(session.id, uuid.uuid4())
        recent = sm.recent_activity(session.id, limit=10)
        kinds = [a.kind for a in recent]
        assert ActivityKind.SESSION_STARTED in kinds
        assert ActivityKind.APPLICATION_CHANGED in kinds
        assert ActivityKind.MISSION_CHANGED in kinds

    async def test_recent_activity_ring_is_bounded(self) -> None:
        sm = build_session_manager(recent_activity_capacity=3)
        session = await sm.start_session(user_id="u")
        for i in range(10):
            await sm.set_current_application(session.id, f"app_{i}")
        recent = sm.recent_activity(session.id, limit=10)
        assert len(recent) <= 3

    async def test_recent_activity_unknown_session_raises(self) -> None:
        sm = build_session_manager()
        with pytest.raises(UnknownSessionError):
            sm.recent_activity(uuid.uuid4())


# ---------------------------------------------------------------------- #
# Persistence
# ---------------------------------------------------------------------- #
class TestPersistence:
    async def test_persist_and_restore(self) -> None:
        from hermes.modules.session_manager.service import InMemorySessionStore

        clock = FakeClock()
        shared_store = InMemorySessionStore()
        sm = build_session_manager(clock=clock, store=shared_store)
        session = await sm.start_session(user_id="alice")
        await sm.set_current_application(session.id, "mission_control")
        await sm.persist(session.id)

        # Build a fresh manager backed by the same store.
        sm2 = build_session_manager(clock=clock, store=shared_store)
        restored = await sm2.restore(session.id)
        assert restored is not None
        assert restored.user_id == "alice"
        assert restored.current_application_id == "mission_control"

    async def test_restore_unknown_returns_none(self) -> None:
        sm = build_session_manager()
        assert await sm.restore(uuid.uuid4()) is None

    async def test_persist_unknown_raises(self) -> None:
        sm = build_session_manager()
        with pytest.raises(UnknownSessionError):
            await sm.persist(uuid.uuid4())


# ---------------------------------------------------------------------- #
# Events + protocol
# ---------------------------------------------------------------------- #
class TestEventsAndProtocol:
    async def test_start_session_publishes_event(self) -> None:
        bus = InMemoryEventBus()
        sm = build_session_manager(event_bus=bus)
        captured: list = []

        async def handler(ev):  # type: ignore[no-untyped-def]
            captured.append(ev)

        await bus.subscribe("session_manager.session.started", handler)
        await sm.start_session(user_id="u")
        assert len(captured) == 1

    async def test_end_session_publishes_event(self) -> None:
        bus = InMemoryEventBus()
        sm = build_session_manager(event_bus=bus)
        session = await sm.start_session(user_id="u")
        captured: list = []

        async def handler(ev):  # type: ignore[no-untyped-def]
            captured.append(ev)

        await bus.subscribe("session_manager.session.ended", handler)
        await sm.end_session(session.id)
        assert len(captured) == 1

    def test_satisfies_session_manager_protocol(self) -> None:
        sm = build_session_manager()
        assert isinstance(sm, SessionManagerProtocol)

    async def test_workspace_accessor_protocol_satisfied_by_manager(
        self,
    ) -> None:
        ws = build_workspace_manager()
        sm = build_session_manager(workspace_manager=ws)
        # If `WorkspaceAccessor` is satisfied, session manager
        # should be able to call `get_workspace` on it.
        assert isinstance(ws, WorkspaceAccessor)