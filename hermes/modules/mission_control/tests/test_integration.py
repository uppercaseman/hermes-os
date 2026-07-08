"""Mission Control end-to-end integration test.

Wires all five workspace modules together with an
`InMemoryEventBus`, publishes lifecycle events, and asserts that
the cross-module effects ripple correctly:

- Application Registry: notifications reflect application changes.
- Workspace Manager: workspace lifecycle is recorded.
- Session Manager: current-X pointers reflect activity.
- Notification Center: bus events surface as notifications.
- Mission Control: timeline + summaries reflect activity.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, Field

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.core.event_bus.models import Event
from hermes.modules.application_registry import build_application_registry
from hermes.modules.mission_control import build_mission_control
from hermes.modules.notification_center import build_notification_center
from hermes.modules.session_manager import build_session_manager
from hermes.modules.workspace_manager import build_workspace_manager


class _Mission(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    goal: str
    status: str
    assigned_team: list = Field(default_factory=list)
    success_criteria: list = Field(default_factory=list)
    outputs: dict = Field(default_factory=dict)
    requested_roles: list = Field(default_factory=list)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class _Source:
    def __init__(self, missions: list[_Mission]) -> None:
        self._missions = list(missions)

    def list_missions(self) -> list[_Mission]:
        return list(self._missions)

    def get_mission(self, mid: uuid.UUID) -> _Mission | None:
        return next((m for m in self._missions if m.id == mid), None)


@pytest.fixture
async def env():
    bus = InMemoryEventBus()

    app_registry = build_application_registry(event_bus=bus)
    workspace_manager = build_workspace_manager(
        event_bus=bus, application_registry=app_registry
    )
    session_manager = build_session_manager(
        event_bus=bus, workspace_manager=workspace_manager
    )
    notification_center = build_notification_center(event_bus=bus)
    await notification_center.start()

    source = _Source(
        missions=[
            _Mission(
                id=uuid.uuid4(),
                goal="first mission",
                status="running",
            ),
            _Mission(
                id=uuid.uuid4(),
                goal="second mission",
                status="completed",
            ),
        ]
    )
    mission_control = build_mission_control(
        mission_source=source, event_bus=bus
    )
    await mission_control.start()

    return {
        "bus": bus,
        "app_registry": app_registry,
        "workspace_manager": workspace_manager,
        "session_manager": session_manager,
        "notification_center": notification_center,
        "mission_control": mission_control,
        "source": source,
    }


# ---------------------------------------------------------------------- #
class TestWorkspaceLifecycleNotifies:
    async def test_workspace_create_emits_notification(self, env) -> None:
        workspace = await env["workspace_manager"].create_workspace(
            name="Default", owner="alice"
        )
        notifications = env["notification_center"].list_notifications()
        # The Notification Center should have seen the
        # workspace_manager.workspace.created event.
        assert any(
            "workspace.created" in n.source_event_type
            for n in notifications
        )
        # Notification Center has the workspace ID embedded.
        events = [n for n in notifications if n.source_event_type and "workspace.created" in n.source_event_type]
        assert any(
            str(workspace.id) in (n.body or "")
            for n in events
        )


class TestSessionLifecycleNotifies:
    async def test_session_started_emits_notification(self, env) -> None:
        session = await env["session_manager"].start_session(user_id="alice")
        notifications = env["notification_center"].list_notifications()
        events = [
            n
            for n in notifications
            if n.source_event_type == "session_manager.session.started"
        ]
        assert len(events) == 1
        assert str(session.id) in (events[0].body or "")


class TestMissionSummaryPublishesNotification:
    async def test_mission_summary_view_publishes_event(
        self, env
    ) -> None:
        mission = env["source"].list_missions()[0]
        await env["mission_control"].mission_summary(mission.id)
        notifications = env["notification_center"].list_notifications()
        events = [
            n
            for n in notifications
            if n.source_event_type
            == "mission_control.view.mission_summary_viewed"
        ]
        assert len(events) == 1


class TestApplicationRegistryLifecycleNotifies:
    async def test_register_application_async_notifies(self, env) -> None:
        from hermes.modules.application_registry.models import Application

        await env["app_registry"].register_application_async(
            Application(
                id="custom_app",
                name="Custom",
                description="x",
                category="custom",
            )
        )
        notifications = env["notification_center"].list_notifications()
        events = [
            n
            for n in notifications
            if n.source_event_type
            == "application_registry.application.registered"
        ]
        assert len(events) == 1


class TestWorkspaceLifecycleThroughSession:
    async def test_full_session_workflow(self, env) -> None:
        workspace = await env["workspace_manager"].create_workspace(
            name="W", owner="alice"
        )
        await env["workspace_manager"].set_current_workspace(workspace.id)
        await env["workspace_manager"].set_current_application(
            workspace.id, "mission_control"
        )
        session = await env["session_manager"].start_session(user_id="alice")
        await env["session_manager"].set_current_workspace(
            session.id, workspace.id
        )
        # The session's current_workspace_id should match.
        s = await env["session_manager"].get_session(session.id)
        assert s is not None
        assert s.current_workspace_id == workspace.id
        # The session's recent_activity should include the workspace change.
        recent = env["session_manager"].recent_activity(session.id, limit=10)
        kinds = [a.kind for a in recent]
        assert "session_started" in kinds
        assert "workspace_changed" in kinds


class TestEndToEndWorkspaceAndMission:
    async def test_workspace_open_mission_records_timeline(
        self, env
    ) -> None:
        workspace = await env["workspace_manager"].create_workspace(
            name="W", owner="alice"
        )
        mission = env["source"].list_missions()[0]
        await env["workspace_manager"].open_mission(workspace.id, mission.id)
        # Mission timeline should not yet have any entries (we didn't
        # publish events with this mission's correlation_id). We just
        # confirm the open_mission flow does not error.
        timeline = env["mission_control"].mission_timeline(mission.id)
        assert timeline == []


class TestBusIsSharedAcrossModules:
    async def test_bus_receives_every_module_publication(self, env) -> None:
        captured: list = []

        async def handler(ev) -> None:  # type: ignore[no-untyped-def]
            captured.append(ev)

        await env["bus"].subscribe("*", handler)

        workspace = await env["workspace_manager"].create_workspace(
            name="W", owner="alice"
        )
        session = await env["session_manager"].start_session(user_id="alice")
        mission = env["source"].list_missions()[0]
        await env["mission_control"].mission_summary(mission.id)

        types = {ev.event_type for ev in captured}
        assert "workspace_manager.workspace.created" in types
        assert "session_manager.session.started" in types
        assert "mission_control.view.mission_summary_viewed" in types


class TestNotificationSeverityForMissionFailure:
    async def test_mission_failed_event_yields_error_notification(
        self, env
    ) -> None:
        mission = env["source"].list_missions()[0]
        await env["bus"].publish(
            Event(
                event_type="mission.failed",
                source_module="mission_system",
                correlation_id=mission.id,
                payload={"reason": "boom"},
            )
        )
        notifications = env["notification_center"].list_notifications(
            severity="error"
        )
        assert len(notifications) >= 1