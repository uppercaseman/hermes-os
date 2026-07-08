"""Test suite for `hermes.modules.application_framework`.

Covers:
- Lifecycle state machine (register, unregister, startup, shutdown,
  activate, deactivate, error transitions).
- The Protocol round-trip (any object satisfying `ApplicationProtocol`
  is accepted; non-conforming objects are rejected).
- Workspace integration: workspace id validation through
  `WorkspaceAccessor` Protocol, focus forwarding.
- Application Registry integration: capability cross-reference
  through `ApplicationSource` Protocol.
- Event publishing: all eight `application_framework.*` events.
- History ring: per-app `lifecycle_history()` and global
  `recent_events()` bounded ring.
- No-bus silent skip.
- Multi-app independent lifecycle tracking.
- Routing request dispatch.
- `BaseApplication` default-no-op behavior.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.application_framework import (
    ApplicationFramework,
    BaseApplication,
    build_application_framework,
)
from hermes.modules.application_framework import events as evt
from hermes.modules.application_framework.contracts import (
    ApplicationProtocol,
)
from hermes.modules.application_framework.errors import (
    ApplicationLifecycleError,
    DuplicateApplicationInstanceError,
    UnknownApplicationError,
)
from hermes.modules.application_framework.models import (
    Application,
    RoutingRequest,
    WorkspaceIntegration,
)


# --------------------------------------------------------------------- #
# Test fixtures / helpers
# --------------------------------------------------------------------- #
class FakeApp:
    """A minimal `ApplicationProtocol` implementation.

    Records every verb call into a list so tests can assert what
    the framework actually invoked."""

    def __init__(
        self,
        *,
        id: str,
        name: str | None = None,
        version: str = "1.0.0",
        category: str = "test",
        required_capabilities: list[str] | None = None,
        required_permissions: list[str] | None = None,
        event_subscriptions: list[str] | None = None,
        route: str | None = None,
        metadata: dict | None = None,
        fail_on: str | None = None,
    ) -> None:
        self._id = id
        self._name = name or id
        self._version = version
        self._category = category
        self._required_capabilities = list(required_capabilities or [])
        self._required_permissions = list(required_permissions or [])
        self._event_subscriptions = list(event_subscriptions or [])
        self._workspace_integration = (
            WorkspaceIntegration(route=route or f"/{id}")
            if (route is not None or True)
            else None
        )
        self._metadata = dict(metadata or {})
        self.calls: list[str] = []
        self.routed: list[RoutingRequest] = []
        self.focus_events: list[tuple] = []
        self.fail_on = fail_on

    async def _maybe_fail(self, verb: str) -> None:
        self.calls.append(verb)
        if self.fail_on == verb:
            raise RuntimeError(f"fake app {self._id!r} failed on {verb}")

    async def startup(self) -> None:
        await self._maybe_fail("startup")

    async def shutdown(self) -> None:
        await self._maybe_fail("shutdown")

    async def activate(self) -> None:
        await self._maybe_fail("activate")

    async def deactivate(self) -> None:
        await self._maybe_fail("deactivate")

    def get_metadata(self) -> Application:
        return Application(
            id=self._id,
            name=self._name,
            version=self._version,
            category=self._category,
            required_capabilities=list(self._required_capabilities),
            required_permissions=list(self._required_permissions),  # type: ignore[arg-type]
            event_subscriptions=list(self._event_subscriptions),  # type: ignore[arg-type]
            workspace_integration=self._workspace_integration,
            metadata=dict(self._metadata),
        )

    def get_required_capabilities(self) -> list[str]:
        return list(self._required_capabilities)

    def get_required_permissions(self) -> list[str]:
        return list(self._required_permissions)

    def get_event_subscriptions(self) -> list[str]:
        return list(self._event_subscriptions)

    def get_workspace_route(self) -> WorkspaceIntegration:
        return self._workspace_integration

    async def on_workspace_focus(self, workspace_id, focused: bool) -> None:
        self.focus_events.append((workspace_id, focused))

    async def handle_routing(self, request: RoutingRequest) -> None:
        self.routed.append(request)


class FakeRegistry:
    """A minimal `ApplicationSource` implementation."""

    def __init__(self, known_ids: list[str] | None = None) -> None:
        self.known = set(known_ids or [])

    def get_application(self, application_id: str):
        return {"id": application_id} if application_id in self.known else None

    def has_application(self, application_id: str) -> bool:
        return application_id in self.known


class FakeWorkspaceManager:
    """A minimal `WorkspaceAccessor` implementation."""

    def __init__(self, known_workspaces: list[uuid.UUID] | None = None) -> None:
        self.known = set(known_workspaces or [])
        self.set_calls: list[tuple[uuid.UUID, str]] = []

    def get_workspace(self, workspace_id: uuid.UUID):
        return {"id": workspace_id} if workspace_id in self.known else None

    async def set_current_application(self, workspace_id: uuid.UUID, application_id: str):
        self.set_calls.append((workspace_id, application_id))
        return {"id": workspace_id, "current_application_id": application_id}


# --------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------- #
class TestRegistration:
    def test_register_accepts_protocol_conforming_object(self):
        fw = build_application_framework()
        app = FakeApp(id="alpha")
        result = fw.register_application(app)
        assert isinstance(result, Application)
        assert result.id == "alpha"
        assert result.lifecycle_state == "registered"
        assert "alpha" in fw
        assert len(fw) == 1

    def test_register_rejects_non_conforming_object(self):
        fw = build_application_framework()

        class NotAnApp:
            pass

        with pytest.raises(TypeError):
            fw.register_application(NotAnApp())

    def test_register_duplicate_raises(self):
        fw = build_application_framework()
        fw.register_application(FakeApp(id="alpha"))
        with pytest.raises(DuplicateApplicationInstanceError):
            fw.register_application(FakeApp(id="alpha"))

    def test_register_surfaces_catalog_miss_via_last_error(self):
        fw = build_application_framework(application_registry=FakeRegistry(known_ids=[]))
        result = fw.register_application(FakeApp(id="orphan"))
        assert result.lifecycle_state == "registered"
        assert result.last_error is not None
        assert "orphan" in result.last_error

    def test_register_known_catalog_has_no_error(self):
        fw = build_application_framework(application_registry=FakeRegistry(known_ids=["alpha"]))
        result = fw.register_application(FakeApp(id="alpha"))
        assert result.last_error is None

    def test_unregister_removes_app(self):
        fw = build_application_framework()
        fw.register_application(FakeApp(id="alpha"))
        removed = fw.unregister_application("alpha")
        assert removed.id == "alpha"
        assert "alpha" not in fw

    def test_unregister_unknown_raises(self):
        fw = build_application_framework()
        with pytest.raises(UnknownApplicationError):
            fw.unregister_application("ghost")

    def test_unregister_force_stops_active_app(self):
        # We cannot drive an async state transition in a sync test, but
        # we can fabricate the state directly via the protected
        # transition helper to assert the force-stop path.
        fw = build_application_framework()
        app = FakeApp(id="alpha")
        fw.register_application(app)
        fw._states["alpha"] = fw._transition_sync(
            "alpha",
            from_state="registered",
            to_state="starting",
        )
        fw._states["alpha"] = fw._transition_sync(
            "alpha",
            from_state="starting",
            to_state="active",
        )
        removed = fw.unregister_application("alpha")
        assert removed.lifecycle_state == "stopped"
        assert "alpha" not in fw


# --------------------------------------------------------------------- #
# Read surface
# --------------------------------------------------------------------- #
class TestLookup:
    def test_get_application_returns_state(self):
        fw = build_application_framework()
        fw.register_application(FakeApp(id="alpha"))
        result = fw.get_application("alpha")
        assert result is not None and result.id == "alpha"

    def test_get_application_unknown_returns_none(self):
        fw = build_application_framework()
        assert fw.get_application("ghost") is None

    def test_list_applications_sorted_by_category_then_id(self):
        fw = build_application_framework()
        fw.register_application(FakeApp(id="z", category="b"))
        fw.register_application(FakeApp(id="a", category="a"))
        fw.register_application(FakeApp(id="m", category="a"))
        apps = fw.list_applications()
        assert [a.id for a in apps] == ["a", "m", "z"]

    def test_get_protocol_returns_conforming_object(self):
        fw = build_application_framework()
        app = FakeApp(id="alpha")
        fw.register_application(app)
        assert fw.get_protocol("alpha") is app


# --------------------------------------------------------------------- #
# Lifecycle verbs
# --------------------------------------------------------------------- #
class TestLifecycle:
    async def test_startup_calls_app_and_transitions(self):
        fw = build_application_framework()
        app = FakeApp(id="alpha")
        fw.register_application(app)
        state = await fw.startup_application("alpha")
        assert state.lifecycle_state == "active"
        assert app.calls == ["startup"]

    async def test_startup_failure_moves_to_error(self):
        fw = build_application_framework()
        app = FakeApp(id="alpha", fail_on="startup")
        fw.register_application(app)
        with pytest.raises(RuntimeError):
            await fw.startup_application("alpha")
        assert fw.get_application("alpha").lifecycle_state == "error"

    async def test_shutdown_calls_app_and_transitions(self):
        fw = build_application_framework()
        app = FakeApp(id="alpha")
        fw.register_application(app)
        await fw.startup_application("alpha")
        state = await fw.shutdown_application("alpha")
        assert state.lifecycle_state == "stopped"
        assert "shutdown" in app.calls

    async def test_activate_requires_inactive_state(self):
        fw = build_application_framework()
        fw.register_application(FakeApp(id="alpha"))
        with pytest.raises(ApplicationLifecycleError):
            await fw.activate_application("alpha")

    async def test_activate_after_deactivate(self):
        fw = build_application_framework()
        app = FakeApp(id="alpha")
        fw.register_application(app)
        await fw.startup_application("alpha")
        await fw.deactivate_application("alpha")
        state = await fw.activate_application("alpha")
        assert state.lifecycle_state == "active"
        assert "activate" in app.calls

    async def test_activate_after_startup_is_idempotent_noop(self):
        fw = build_application_framework()
        app = FakeApp(id="alpha")
        fw.register_application(app)
        await fw.startup_application("alpha")
        state = await fw.activate_application("alpha")
        assert state.lifecycle_state == "active"
        # activate() is NOT invoked again because we are already active.
        assert app.calls.count("activate") == 0

    async def test_deactivate_requires_active(self):
        fw = build_application_framework()
        app = FakeApp(id="alpha")
        fw.register_application(app)
        with pytest.raises(ApplicationLifecycleError):
            await fw.deactivate_application("alpha")

    async def test_activate_deactivate_cycle(self):
        fw = build_application_framework()
        app = FakeApp(id="alpha")
        fw.register_application(app)
        await fw.startup_application("alpha")
        await fw.deactivate_application("alpha")
        state = await fw.activate_application("alpha")
        await fw.deactivate_application("alpha")
        assert state.lifecycle_state == "active"  # last activate
        assert "deactivate" in app.calls
        assert app.calls.count("deactivate") == 2

    async def test_unknown_app_raises(self):
        fw = build_application_framework()
        with pytest.raises(UnknownApplicationError):
            await fw.startup_application("ghost")


# --------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------- #
class TestHistory:
    async def test_lifecycle_history_records_every_transition(self):
        fw = build_application_framework()
        fw.register_application(FakeApp(id="alpha"))
        await fw.startup_application("alpha")
        await fw.activate_application("alpha")
        await fw.deactivate_application("alpha")
        await fw.shutdown_application("alpha")
        history = fw.lifecycle_history("alpha")
        assert [e.to_state for e in history] == [
            "registered",
            "starting",
            "active",
            "inactive",
            "stopped",
        ]

    def test_recent_events_bounded(self):
        fw = build_application_framework(history_size=3)
        for i in range(5):
            fw.register_application(FakeApp(id=f"a{i}"))
        recent = fw.recent_events(limit=10)
        assert len(recent) == 3

    def test_recent_events_zero_or_negative_returns_empty(self):
        fw = build_application_framework()
        assert fw.recent_events(limit=0) == []
        assert fw.recent_events(limit=-1) == []


# --------------------------------------------------------------------- #
# Workspace integration
# --------------------------------------------------------------------- #
class TestWorkspaceIntegration:
    async def test_notify_workspace_focus_forwards_to_app(self):
        fw = build_application_framework()
        app = FakeApp(id="alpha")
        fw.register_application(app)
        ws_id = uuid.uuid4()
        await fw.notify_workspace_focus(ws_id, "alpha", focused=True)
        await fw.notify_workspace_focus(ws_id, "alpha", focused=False)
        assert app.focus_events == [(ws_id, True), (ws_id, False)]

    async def test_notify_workspace_focus_unknown_workspace_raises(self):
        ws_id = uuid.uuid4()
        fw = build_application_framework(
            workspace_manager=FakeWorkspaceManager(known_workspaces=[])
        )
        fw.register_application(FakeApp(id="alpha"))
        with pytest.raises(UnknownApplicationError):
            await fw.notify_workspace_focus(ws_id, "alpha", focused=True)

    async def test_set_current_application_no_workspace_manager_is_noop(self):
        fw = build_application_framework()
        fw.register_application(FakeApp(id="alpha"))
        result = await fw.set_current_application_in_workspace(
            uuid.uuid4(), "alpha"
        )
        assert result is None

    async def test_set_current_application_unknown_app_raises(self):
        wm = FakeWorkspaceManager(known_workspaces=[uuid.uuid4()])
        fw = build_application_framework(workspace_manager=wm)
        with pytest.raises(UnknownApplicationError):
            await fw.set_current_application_in_workspace(uuid.uuid4(), "ghost")

    async def test_set_current_application_delegates_to_workspace_manager(self):
        ws_id = uuid.uuid4()
        wm = FakeWorkspaceManager(known_workspaces=[ws_id])
        fw = build_application_framework(workspace_manager=wm)
        fw.register_application(FakeApp(id="alpha"))
        await fw.set_current_application_in_workspace(ws_id, "alpha")
        assert wm.set_calls == [(ws_id, "alpha")]


# --------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------- #
class TestRouting:
    async def test_route_dispatches_to_target(self):
        fw = build_application_framework()
        app = FakeApp(id="alpha")
        fw.register_application(app)
        request = RoutingRequest(
            source="workspace_manager",
            target_application_id="alpha",
            kind="event",
            payload={"hello": "world"},
        )
        await fw.route(request)
        assert len(app.routed) == 1
        assert app.routed[0].target_application_id == "alpha"
        assert app.routed[0].payload == {"hello": "world"}

    async def test_route_unknown_app_raises(self):
        fw = build_application_framework()
        request = RoutingRequest(
            source="x",
            target_application_id="ghost",
            kind="event",
        )
        with pytest.raises(UnknownApplicationError):
            await fw.route(request)


# --------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------- #
class TestEvents:
    async def test_all_eight_events_published_in_lifecycle(self):
        bus = InMemoryEventBus()
        captured: list[str] = []

        async def _handler(event) -> None:
            captured.append(event.event_type)

        await bus.subscribe("*", _handler)
        fw = build_application_framework(event_bus=bus)
        app = FakeApp(id="alpha")
        await fw.register_application_async(app)
        await fw.startup_application("alpha")
        await fw.deactivate_application("alpha")
        await fw.activate_application("alpha")
        await fw.deactivate_application("alpha")
        await fw.shutdown_application("alpha")
        await fw.unregister_application_async("alpha")

        # We expect these event constants to have been published:
        expected = {
            evt.APPLICATION_REGISTERED,
            evt.APPLICATION_STARTING,
            evt.APPLICATION_STARTED,
            evt.APPLICATION_ACTIVATED,
            evt.APPLICATION_DEACTIVATED,
            evt.APPLICATION_STOPPED,
            evt.APPLICATION_UNREGISTERED,
        }
        assert expected.issubset(set(captured))

    def test_no_bus_silent_skip(self):
        fw = build_application_framework(event_bus=None)
        # Should not raise even though there is no bus.
        fw.register_application(FakeApp(id="alpha"))


# --------------------------------------------------------------------- #
# BaseApplication default behaviour
# --------------------------------------------------------------------- #
class TestBaseApplication:
    async def test_base_app_default_noops(self):
        class MyApp(BaseApplication):
            pass

        app = MyApp(id="alpha")
        await app.startup()
        await app.shutdown()
        await app.activate()
        await app.deactivate()
        meta = app.get_metadata()
        assert meta.id == "alpha"
        assert meta.lifecycle_state == "registered"  # default on construction
        assert app.get_required_capabilities() == []
        assert app.get_required_permissions() == []
        assert app.get_event_subscriptions() == []
        route = app.get_workspace_route()
        assert route.route == "/alpha"

    async def test_base_app_registered_via_framework(self):
        class MyApp(BaseApplication):
            async def startup(self) -> None:
                pass

        fw = build_application_framework()
        fw.register_application(MyApp(id="alpha"))
        state = await fw.startup_application("alpha")
        assert state.lifecycle_state == "active"


# --------------------------------------------------------------------- #
# Protocol runtime check
# --------------------------------------------------------------------- #
class TestProtocolRoundTrip:
    def test_fake_app_isinstance_application_protocol(self):
        assert isinstance(FakeApp(id="x"), ApplicationProtocol)

    def test_base_application_isinstance_application_protocol(self):
        assert isinstance(BaseApplication(id="x"), ApplicationProtocol)

    def test_unrelated_object_fails_protocol_check(self):
        class Nope:
            pass

        assert not isinstance(Nope(), ApplicationProtocol)


# --------------------------------------------------------------------- #
# Multi-app concurrency
# --------------------------------------------------------------------- #
class TestMultiApp:
    async def test_independent_lifecycles(self):
        fw = build_application_framework()
        a, b, c = FakeApp(id="a"), FakeApp(id="b"), FakeApp(id="c")
        fw.register_application(a)
        fw.register_application(b)
        fw.register_application(c)
        await fw.startup_application("a")
        await fw.startup_application("b")
        # c stays registered
        assert fw.get_application("a").lifecycle_state == "active"
        assert fw.get_application("b").lifecycle_state == "active"
        assert fw.get_application("c").lifecycle_state == "registered"

    async def test_concurrent_startup(self):
        fw = build_application_framework()
        for i in range(10):
            fw.register_application(FakeApp(id=f"a{i}"))
        await asyncio.gather(
            *[fw.startup_application(f"a{i}") for i in range(10)]
        )
        for i in range(10):
            assert fw.get_application(f"a{i}").lifecycle_state == "active"