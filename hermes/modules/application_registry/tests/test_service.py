"""Application Registry unit tests.

Strategy: build the registry with no bus (silent skip) for the bulk
of tests, plus one EventBus test class that exercises the
`register_application_async` / `remove_application_async` /
`set_application_status_async` paths.
"""
from __future__ import annotations

import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.application_registry import build_application_registry
from hermes.modules.application_registry.contracts import ApplicationSource
from hermes.modules.application_registry.errors import (
    ApplicationNotFoundError,
    DuplicateApplicationError,
)
from hermes.modules.application_registry.models import Application


# ---------------------------------------------------------------------- #
# Defaults
# ---------------------------------------------------------------------- #
class TestDefaults:
    def test_eight_default_applications_are_registered(self) -> None:
        reg = build_application_registry()
        assert len(reg) == 8

    def test_default_ids(self) -> None:
        reg = build_application_registry()
        expected = {
            "mission_control",
            "memory_galaxy",
            "developer_studio",
            "executive_dashboard",
            "knowledge_explorer",
            "automation_center",
            "provider_manager",
            "settings",
        }
        assert set(reg) == expected

    def test_auto_register_defaults_false_yields_empty(self) -> None:
        reg = build_application_registry(auto_register_defaults=False)
        assert len(reg) == 0
        assert reg.list_applications() == []

    def test_contains_membership(self) -> None:
        reg = build_application_registry()
        assert "mission_control" in reg
        assert "missing" not in reg
        # Non-string membership returns False, never raises.
        assert (123 in reg) is False


# ---------------------------------------------------------------------- #
# Lookups
# ---------------------------------------------------------------------- #
class TestLookups:
    def test_get_application_returns_record(self) -> None:
        reg = build_application_registry()
        app = reg.get_application("mission_control")
        assert app is not None
        assert app.name == "Mission Control"
        assert app.category == "mission_control"
        assert app.status == "active"

    def test_get_application_missing_returns_none(self) -> None:
        reg = build_application_registry()
        assert reg.get_application("nope") is None

    def test_has_application(self) -> None:
        reg = build_application_registry()
        assert reg.has_application("settings") is True
        assert reg.has_application("nope") is False

    def test_list_applications_sorted_by_category_then_id(self) -> None:
        reg = build_application_registry()
        apps = reg.list_applications()
        for prev, curr in zip(apps, apps[1:]):
            assert (prev.category, prev.id) <= (curr.category, curr.id)

    def test_list_applications_filter_by_category(self) -> None:
        reg = build_application_registry()
        memory = reg.list_applications(category="memory")
        assert [a.id for a in memory] == ["memory_galaxy"]
        # No application in this category for the seeded defaults.
        assert reg.list_applications(category="custom") == []


# ---------------------------------------------------------------------- #
# Mutating API
# ---------------------------------------------------------------------- #
class TestMutations:
    def test_register_application_succeeds(self) -> None:
        reg = build_application_registry(auto_register_defaults=False)
        app = Application(
            id="custom", name="Custom", description="x", category="custom"
        )
        registered = reg.register_application(app)
        assert registered is app
        assert reg.get_application("custom") is app

    def test_register_application_duplicate_raises(self) -> None:
        reg = build_application_registry()
        dup = Application(
            id="mission_control", name="X", description="y", category="mission_control"
        )
        with pytest.raises(DuplicateApplicationError):
            reg.register_application(dup)

    def test_remove_application_succeeds(self) -> None:
        reg = build_application_registry()
        removed = reg.remove_application("settings")
        assert removed.id == "settings"
        assert "settings" not in reg

    def test_remove_application_missing_raises(self) -> None:
        reg = build_application_registry()
        with pytest.raises(ApplicationNotFoundError):
            reg.remove_application("nope")

    def test_set_application_status_succeeds(self) -> None:
        reg = build_application_registry()
        updated = reg.set_application_status("mission_control", "inactive")
        assert updated.status == "inactive"
        assert reg.get_application("mission_control").status == "inactive"

    def test_set_application_status_missing_raises(self) -> None:
        reg = build_application_registry()
        with pytest.raises(ApplicationNotFoundError):
            reg.set_application_status("nope", "inactive")


# ---------------------------------------------------------------------- #
# Async + events
# ---------------------------------------------------------------------- #
class TestAsyncAndEvents:
    async def test_register_application_async_publishes_event(self) -> None:
        bus = InMemoryEventBus()
        reg = build_application_registry(
            event_bus=bus, auto_register_defaults=False
        )
        captured: list = []

        async def handler(ev):  # type: ignore[no-untyped-def]
            captured.append(ev)

        await bus.subscribe(
            "application_registry.application.registered", handler
        )
        await reg.register_application_async(
            Application(
                id="custom", name="Custom", description="x", category="custom"
            )
        )
        assert len(captured) == 1
        ev = captured[0]
        assert ev.payload["application_id"] == "custom"
        assert ev.payload["category"] == "custom"

    async def test_remove_application_async_publishes_event(self) -> None:
        bus = InMemoryEventBus()
        reg = build_application_registry(event_bus=bus)
        captured: list = []

        async def handler(ev):  # type: ignore[no-untyped-def]
            captured.append(ev)

        await bus.subscribe(
            "application_registry.application.removed", handler
        )
        await reg.remove_application_async("settings")
        assert len(captured) == 1
        assert captured[0].payload["application_id"] == "settings"

    async def test_set_status_async_publishes_activated_and_deactivated(
        self,
    ) -> None:
        bus = InMemoryEventBus()
        reg = build_application_registry(event_bus=bus)
        activated: list = []
        deactivated: list = []

        async def on_activated(ev):  # type: ignore[no-untyped-def]
            activated.append(ev)

        async def on_deactivated(ev):  # type: ignore[no-untyped-def]
            deactivated.append(ev)

        await bus.subscribe(
            "application_registry.application.activated", on_activated
        )
        await bus.subscribe(
            "application_registry.application.deactivated", on_deactivated
        )
        # active -> inactive
        await reg.set_application_status_async("mission_control", "inactive")
        assert len(deactivated) == 1
        assert deactivated[0].payload["previous_status"] == "active"
        # inactive -> active
        await reg.set_application_status_async("mission_control", "active")
        assert len(activated) == 1
        assert activated[0].payload["previous_status"] == "inactive"

    async def test_set_status_async_no_op_does_not_publish(self) -> None:
        bus = InMemoryEventBus()
        reg = build_application_registry(event_bus=bus)
        activated: list = []

        async def on_activated(ev):  # type: ignore[no-untyped-def]
            activated.append(ev)

        await bus.subscribe(
            "application_registry.application.activated", on_activated
        )
        # already active -> active
        await reg.set_application_status_async("mission_control", "active")
        assert activated == []

    async def test_no_bus_async_mutations_succeed(self) -> None:
        reg = build_application_registry(auto_register_defaults=False)
        await reg.register_application_async(
            Application(
                id="c", name="c", description="c", category="custom"
            )
        )
        assert "c" in reg


# ---------------------------------------------------------------------- #
# Protocol surface
# ---------------------------------------------------------------------- #
class TestProtocolSurface:
    def test_registry_satisfies_application_source_protocol(self) -> None:
        reg = build_application_registry()
        assert isinstance(reg, ApplicationSource)

    def test_application_source_get_returns_none_for_missing(self) -> None:
        reg = build_application_registry()
        source: ApplicationSource = reg
        assert source.get_application("missing") is None
        assert source.has_application("missing") is False
        assert source.list_applications() == reg.list_applications()
