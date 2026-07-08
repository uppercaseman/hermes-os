"""Application Registry -- the catalog of every Hermes application.

The Registry is a thin, deterministic lookup table. It does NOT
launch applications, hold any runtime state, or talk to any module
other than the EventBus (for `application.*` observability events).

Key properties:

- **Default-seeded.** On construction, the registry seeds itself
  with the eight canonical Hermes applications named in the
  Sprint-5 directive: Mission Control, Memory Galaxy, Developer
  Studio, Executive Dashboard, Knowledge Explorer, Automation
  Center, Provider Manager, Settings.
- **Deterministic ordering.** `list_applications()` returns entries
  sorted by `(category, id)` so every caller sees the same order.
- **No shared mutable state.** All mutations go through the
  registry's own API; nothing else mutates the dict.
- **Read-mostly.** The Registry never writes to memory, never
  invokes the capability registry, and never reads from the
  workspace. It is the most-passive module in the workspace layer.
- **Synchronous API.** The Registry owns no I/O of its own. Every
  mutating method comes in two forms: a synchronous form that
  mutates state and returns the resulting record (no event
  publish), and an `async` form that mutates state and awaits the
  `application.*` event publish. Tests can use the sync form when
  they don't care about events.
"""
from __future__ import annotations

import uuid
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.application_registry import events as evt
from hermes.modules.application_registry.errors import (
    ApplicationNotFoundError,
    DuplicateApplicationError,
)
from hermes.modules.application_registry.models import (
    Application,
    ApplicationCategory,
    ApplicationStatus,
)

SOURCE_MODULE = "application_registry"


def _default_applications() -> list[Application]:
    """The eight canonical Hermes applications shipped with Sprint-5."""
    return [
        Application(
            id="mission_control",
            name="Mission Control",
            description="Top-level view of every running, queued, paused, waiting, blocked, completed, failed, cancelled, and archived mission.",
            category="mission_control",
            version="1.0.0",
            route="/mission_control",
            capabilities_required=["mission_aggregation"],
            entrypoint_metadata={"icon": "compass", "launcher": True},
        ),
        Application(
            id="memory_galaxy",
            name="Memory Galaxy",
            description="Interactive 3D visualization of the Memory Graph: episodes, reflections, and superseded entries.",
            category="memory",
            version="1.0.0",
            route="/memory_galaxy",
            capabilities_required=["memory_read"],
            entrypoint_metadata={"icon": "graph", "launcher": True},
        ),
        Application(
            id="developer_studio",
            name="Developer Studio",
            description="IDE surface for inspecting and editing Hermes specs, plans, modules, and tests.",
            category="developer",
            version="1.0.0",
            route="/developer_studio",
            capabilities_required=["spec_read", "spec_write"],
            entrypoint_metadata={"icon": "code", "launcher": True},
        ),
        Application(
            id="executive_dashboard",
            name="Executive Dashboard",
            description="High-level operational dashboard: KPIs, mission throughput, provider health, and module maturity.",
            category="dashboard",
            version="1.0.0",
            route="/executive_dashboard",
            capabilities_required=["dashboard_read"],
            entrypoint_metadata={"icon": "chart", "launcher": True},
        ),
        Application(
            id="knowledge_explorer",
            name="Knowledge Explorer",
            description="Browse and query the Knowledge Graph and Context Builder surfaces built by the Reasoning Engine.",
            category="knowledge",
            version="1.0.0",
            route="/knowledge_explorer",
            capabilities_required=["knowledge_read"],
            entrypoint_metadata={"icon": "tree", "launcher": True},
        ),
        Application(
            id="automation_center",
            name="Automation Center",
            description="Design, schedule, and monitor Hermes workflows and task-queue jobs.",
            category="automation",
            version="1.0.0",
            route="/automation_center",
            capabilities_required=["workflow_read", "workflow_write"],
            entrypoint_metadata={"icon": "lightning", "launcher": True},
        ),
        Application(
            id="provider_manager",
            name="Provider Manager",
            description="Manage provider adapters (OpenAI, Anthropic, Gemini, Ollama, MCP) and view their live health / cost.",
            category="provider",
            version="1.0.0",
            route="/provider_manager",
            capabilities_required=["provider_read"],
            entrypoint_metadata={"icon": "plug", "launcher": True},
        ),
        Application(
            id="settings",
            name="Settings",
            description="Configure Hermes workspace, session, notifications, theme, and accessibility.",
            category="settings",
            version="1.0.0",
            route="/settings",
            capabilities_required=[],
            entrypoint_metadata={"icon": "gear", "launcher": True},
        ),
    ]


class ApplicationRegistry:
    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        auto_register_defaults: bool = True,
    ) -> None:
        self._bus = event_bus
        self._apps: dict[str, Application] = {}
        if auto_register_defaults:
            for app in _default_applications():
                self._apps[app.id] = app

    # ------------------------------------------------------------------ #
    # Read-only lookup surface
    # ------------------------------------------------------------------ #
    def get_application(self, application_id: str) -> Application | None:
        return self._apps.get(application_id)

    def has_application(self, application_id: str) -> bool:
        return application_id in self._apps

    def list_applications(
        self,
        *,
        category: ApplicationCategory | None = None,
    ) -> list[Application]:
        """Returns every registered application sorted by `(category, id)`.
        When `category` is set, filters first."""
        apps = list(self._apps.values())
        if category is not None:
            apps = [a for a in apps if a.category == category]
        apps.sort(key=lambda a: (a.category, a.id))
        return apps

    def __len__(self) -> int:
        return len(self._apps)

    def __iter__(self):
        return iter(self._apps)

    def __contains__(self, application_id: object) -> bool:
        return isinstance(application_id, str) and application_id in self._apps

    # ------------------------------------------------------------------ #
    # Mutating API
    # ------------------------------------------------------------------ #
    def register_application(self, application: Application) -> Application:
        """Registers `application` synchronously. Raises
        `DuplicateApplicationError` if the id is already in the
        catalog. Does NOT publish events -- use
        `register_application_async` if events are required."""
        if application.id in self._apps:
            raise DuplicateApplicationError(application.id)
        self._apps[application.id] = application
        return application

    async def register_application_async(self, application: Application) -> Application:
        """Async variant of `register_application` that publishes the
        corresponding `APPLICATION_REGISTERED` event."""
        result = self.register_application(application)
        await self._publish(
            evt.APPLICATION_REGISTERED,
            {
                "application_id": application.id,
                "name": application.name,
                "category": application.category,
                "version": application.version,
            },
        )
        return result

    def remove_application(self, application_id: str) -> Application:
        """Removes and returns the application with this id
        synchronously. Raises `ApplicationNotFoundError` if no such id
        is registered. Use `remove_application_async` to publish."""
        try:
            return self._apps.pop(application_id)
        except KeyError as exc:
            raise ApplicationNotFoundError(application_id) from exc

    async def remove_application_async(self, application_id: str) -> Application:
        removed = self.remove_application(application_id)
        await self._publish(
            evt.APPLICATION_REMOVED,
            {
                "application_id": removed.id,
                "name": removed.name,
                "category": removed.category,
            },
        )
        return removed

    def set_application_status(
        self,
        application_id: str,
        status: ApplicationStatus,
    ) -> Application:
        """Sets the `status` of `application_id` synchronously. Raises
        `ApplicationNotFoundError` if not present. Use
        `set_application_status_async` to publish events."""
        try:
            app = self._apps[application_id]
        except KeyError as exc:
            raise ApplicationNotFoundError(application_id) from exc
        updated = app.model_copy(update={"status": status})
        self._apps[application_id] = updated
        return updated

    async def set_application_status_async(
        self,
        application_id: str,
        status: ApplicationStatus,
    ) -> Application:
        """Async variant of `set_application_status` that publishes
        either `APPLICATION_ACTIVATED` or `APPLICATION_DEACTIVATED`
        when the status actually changes."""
        try:
            app = self._apps[application_id]
        except KeyError as exc:
            raise ApplicationNotFoundError(application_id) from exc
        previous = app.status
        updated = app.model_copy(update={"status": status})
        self._apps[application_id] = updated
        if previous != status:
            event_name = (
                evt.APPLICATION_ACTIVATED
                if status == "active"
                else evt.APPLICATION_DEACTIVATED
            )
            await self._publish(
                event_name,
                {
                    "application_id": updated.id,
                    "name": updated.name,
                    "previous_status": previous,
                },
            )
        return updated

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


__all__ = ["ApplicationRegistry", "_default_applications"]
