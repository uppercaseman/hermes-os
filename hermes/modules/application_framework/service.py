"""Application Framework -- the canonical runtime model for every Hermes application.

The Framework is the **operating system layer** between Workspace
Manager and every Hermes application. It owns:

- the **lifecycle state machine** for each registered application
  (unregistered -> registered -> starting -> active <-> inactive ->
  stopped, with `error` as a terminal failure state);
- the **declaration of static metadata** (capabilities, permissions,
  event subscriptions, routing context, workspace integration);
- **mediation with Workspace Manager** through the `WorkspaceAccessor`
  Protocol -- the framework calls `set_current_application` on
  workspace activation, never the other way around;
- **mediation with Application Registry** through the `ApplicationSource`
  Protocol -- the framework validates declared capabilities against
  the catalog at registration time, never mutates the catalog;
- **observability** -- publishes `application_framework.*` events on
  every transition and keeps a bounded ring of `LifecycleEvent`
  records.

What the Framework does NOT do: launch a process, render a UI,
hold a network socket, or call any business logic. It is the
runtime contract, not the runtime engine.
"""
from __future__ import annotations

import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.application_framework import events as evt
from hermes.modules.application_framework.contracts import (
    ApplicationFrameworkProtocol,
    ApplicationProtocol,
    ApplicationSource,
    WorkspaceAccessor,
)
from hermes.modules.application_framework.errors import (
    ApplicationLifecycleError,
    DuplicateApplicationInstanceError,
    UnknownApplicationError,
)
from hermes.modules.application_framework.models import (
    Application,
    LifecycleEvent,
    LifecycleState,
)

SOURCE_MODULE = "application_framework"

# Allowed transitions in the lifecycle state machine.
_ALLOWED_TRANSITIONS: dict[LifecycleState, set[LifecycleState]] = {
    "unregistered": {"registered"},
    "registered": {"starting", "stopped"},
    "starting": {"active", "error"},
    "active": {"inactive", "stopped", "error"},
    "inactive": {"active", "stopped", "error"},
    "stopped": {"registered"},
    "error": {"registered", "stopped"},
}


class BaseApplication:
    """A convenience base class for `ApplicationProtocol` implementors.

    Apps that subclass `BaseApplication` get default no-op
    implementations of every Protocol method, which they can
    override. This is **purely a developer convenience** -- the
    framework does NOT require inheritance, only Protocol
    conformance; any object that satisfies `ApplicationProtocol`
    (whether duck-typed or by inheritance) is accepted.

    Per the user's design choice, the `ApplicationProtocol` itself
    is strict (all ten methods required). This base class is the
    escape hatch for simple apps that do not need every verb.
    """

    def __init__(
        self,
        *,
        id: str,
        name: str | None = None,
        version: str = "0.0.0",
        category: str = "custom",
        description: str = "",
        required_capabilities: list[str] | None = None,
        required_permissions: list[str] | None = None,
        event_subscriptions: list[str] | None = None,
        route: str | None = None,
        window_title_pattern: str = "",
        focus_events: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        from hermes.modules.application_framework.models import (
            EventSubscription,
            Permission,
            WorkspaceIntegration,
        )

        self._id = id
        self._name = name if name is not None else id
        self._version = version
        self._category = category
        self._description = description
        self._required_capabilities = list(required_capabilities or [])
        self._required_permissions: list[Permission] = list(required_permissions or [])  # type: ignore[arg-type]
        self._event_subscriptions: list[EventSubscription] = list(event_subscriptions or [])  # type: ignore[arg-type]
        self._workspace_integration = (
            WorkspaceIntegration(
                route=route or f"/{id}",
                window_title_pattern=window_title_pattern,
                focus_events=list(focus_events or []),
            )
            if (route is not None or window_title_pattern or focus_events)
            else None
        )
        self._metadata = dict(metadata or {})

    # --- lifecycle --- #
    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def activate(self) -> None:
        return None

    async def deactivate(self) -> None:
        return None

    # --- static metadata --- #
    def get_metadata(self) -> Application:
        return Application(
            id=self._id,
            name=self._name,
            version=self._version,
            category=self._category,
            description=self._description,
            required_capabilities=list(self._required_capabilities),
            required_permissions=list(self._required_permissions),
            event_subscriptions=list(self._event_subscriptions),
            workspace_integration=self._workspace_integration,
            metadata=dict(self._metadata),
        )

    def get_required_capabilities(self) -> list[str]:
        return list(self._required_capabilities)

    def get_required_permissions(self) -> list[str]:
        return list(self._required_permissions)

    def get_event_subscriptions(self) -> list[str]:
        return list(self._event_subscriptions)

    # --- workspace integration --- #
    def get_workspace_route(self):
        from hermes.modules.application_framework.models import (
            WorkspaceIntegration,
        )

        return self._workspace_integration or WorkspaceIntegration(
            route=f"/{self._id}",
        )

    async def on_workspace_focus(
        self, workspace_id: uuid.UUID, focused: bool
    ) -> None:
        return None

    # --- routing --- #
    async def handle_routing(self, request) -> None:
        return None


class ApplicationFramework:
    """The framework. Holds the in-process state machine, the
    Protocol-mediated handshakes with Workspace Manager and
    Application Registry, and the bounded event ring."""

    def __init__(
        self,
        *,
        workspace_manager: WorkspaceAccessor | None = None,
        application_registry: ApplicationSource | None = None,
        event_bus: EventBus | None = None,
        history_size: int = 256,
    ) -> None:
        self._workspace_manager = workspace_manager
        self._application_registry = application_registry
        self._bus = event_bus
        self._history_size = history_size

        self._apps: dict[str, ApplicationProtocol] = {}
        self._states: dict[str, Application] = {}
        self._history: dict[str, deque[LifecycleEvent]] = {}
        self._event_ring: deque[LifecycleEvent] = deque(maxlen=history_size)

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register_application(
        self, application: ApplicationProtocol
    ) -> Application:
        """Register an object that satisfies `ApplicationProtocol`.

        Performs an `isinstance(application, ApplicationProtocol)`
        runtime check (the Protocol is `@runtime_checkable`). Reads
        static metadata, transitions the state machine to
        `registered`. Does NOT publish an event -- use
        `register_application_async` for the event-publishing variant.
        """
        if not isinstance(application, ApplicationProtocol):
            raise TypeError(
                f"object {application!r} does not satisfy ApplicationProtocol"
            )
        metadata = application.get_metadata()
        app_id = metadata.id
        if app_id in self._apps:
            raise DuplicateApplicationInstanceError(app_id)

        # Validate declared capabilities against the registry when present.
        if self._application_registry is not None:
            catalog_entry = self._application_registry.get_application(app_id)
            if catalog_entry is None:
                # Catalog miss is not fatal -- future plugin installs
                # may register before being catalogued. We surface it
                # in the lifecycle state via `last_error` but still
                # accept the registration.
                metadata = metadata.model_copy(
                    update={
                        "last_error": (
                            f"application id {app_id!r} is not in the "
                            "application_registry catalog"
                        )
                    }
                )

        self._apps[app_id] = application
        new_state = self._transition(
            app_id,
            to_state="registered",
            note="registered with the framework",
            seed_metadata=metadata,
        )
        return new_state

    async def register_application_async(
        self, application: ApplicationProtocol
    ) -> Application:
        """Async variant of `register_application` that publishes
        `application_framework.application.registered` after the
        state transition completes."""
        result = self.register_application(application)
        await self._publish(
            evt.APPLICATION_REGISTERED,
            {"application_id": result.id},
        )
        return result

    def unregister_application(self, application_id: str) -> Application:
        """Remove an application from the framework. Transitions to
        `stopped` first if the app is `active` or `inactive`. Does NOT
        publish an event -- use `unregister_application_async` for
        the event-publishing variant."""
        if application_id not in self._apps:
            raise UnknownApplicationError(application_id)
        # Tear down active state cleanly.
        state = self._states.get(application_id)
        if state is not None and state.lifecycle_state in ("active", "inactive"):
            # Synchronous teardown -- we do not await the app's
            # shutdown() here because the API surface is sync;
            # tests that care about async teardown use the
            # `shutdown_application` verb.
            self._states[application_id] = self._transition_sync(
                application_id,
                from_state=state.lifecycle_state,
                to_state="stopped",
                note="force-stopped on unregister",
            )
        removed_meta = self._states.pop(application_id)
        self._apps.pop(application_id)
        self._history.pop(application_id, None)
        return removed_meta

    async def unregister_application_async(self, application_id: str) -> Application:
        """Async variant of `unregister_application` that publishes
        `application_framework.application.unregistered` after the
        teardown completes."""
        result = self.unregister_application(application_id)
        await self._publish(
            evt.APPLICATION_UNREGISTERED,
            {"application_id": result.id},
        )
        return result

    # ------------------------------------------------------------------ #
    # Read-only lookup
    # ------------------------------------------------------------------ #
    def get_application(self, application_id: str) -> Application | None:
        return self._states.get(application_id)

    def list_applications(self) -> list[Application]:
        return sorted(
            self._states.values(),
            key=lambda a: (a.category, a.id),
        )

    def get_protocol(self, application_id: str) -> ApplicationProtocol | None:
        return self._apps.get(application_id)

    def __contains__(self, application_id: object) -> bool:
        return isinstance(application_id, str) and application_id in self._apps

    def __len__(self) -> int:
        return len(self._apps)

    def lifecycle_history(self, application_id: str) -> list[LifecycleEvent]:
        return list(self._history.get(application_id, []))

    def recent_events(self, limit: int = 50) -> list[LifecycleEvent]:
        if limit <= 0:
            return []
        return list(self._event_ring)[-limit:]

    # ------------------------------------------------------------------ #
    # Lifecycle verbs (async)
    # ------------------------------------------------------------------ #
    async def startup_application(self, application_id: str) -> Application:
        app = self._require(application_id)
        self._transition(application_id, to_state="starting", note="startup requested")
        await self._publish(
            evt.APPLICATION_STARTING,
            {"application_id": application_id},
        )
        try:
            await app.startup()
        except Exception as exc:
            self._transition(
                application_id,
                to_state="error",
                note=f"startup raised: {exc!r}",
            )
            await self._publish(
                evt.APPLICATION_ERROR,
                {"application_id": application_id, "phase": "startup", "error": repr(exc)},
            )
            raise
        result = self._transition(application_id, to_state="active", note="startup completed")
        await self._publish(
            evt.APPLICATION_STARTED,
            {"application_id": application_id},
        )
        return result

    async def shutdown_application(self, application_id: str) -> Application:
        app = self._require(application_id)
        try:
            await app.shutdown()
        except Exception as exc:
            self._transition(
                application_id,
                to_state="error",
                note=f"shutdown raised: {exc!r}",
            )
            await self._publish(
                evt.APPLICATION_ERROR,
                {"application_id": application_id, "phase": "shutdown", "error": repr(exc)},
            )
            raise
        result = self._transition(application_id, to_state="stopped", note="shutdown completed")
        await self._publish(
            evt.APPLICATION_STOPPED,
            {"application_id": application_id},
        )
        return result

    async def activate_application(self, application_id: str) -> Application:
        app = self._require(application_id)
        current = self._states[application_id].lifecycle_state
        if current == "active":
            # Idempotent re-activation: state is already active; do
            # not re-invoke `app.activate()` and do not publish the
            # event again.
            return self._states[application_id]
        if current != "inactive":
            raise ApplicationLifecycleError(
                application_id,
                current,
                "active",
                reason="activate requires current state == inactive (use startup_application for first activation)",
            )
        try:
            await app.activate()
        except Exception as exc:
            self._transition(
                application_id,
                to_state="error",
                note=f"activate raised: {exc!r}",
            )
            await self._publish(
                evt.APPLICATION_ERROR,
                {"application_id": application_id, "phase": "activate", "error": repr(exc)},
            )
            raise
        result = self._transition(application_id, to_state="active", note="activated")
        await self._publish(
            evt.APPLICATION_ACTIVATED,
            {"application_id": application_id},
        )
        return result

    async def deactivate_application(self, application_id: str) -> Application:
        app = self._require(application_id)
        current = self._states[application_id].lifecycle_state
        if current != "active":
            raise ApplicationLifecycleError(
                application_id,
                current,
                "inactive",
                reason="deactivate requires current state == active",
            )
        try:
            await app.deactivate()
        except Exception as exc:
            self._transition(
                application_id,
                to_state="error",
                note=f"deactivate raised: {exc!r}",
            )
            await self._publish(
                evt.APPLICATION_ERROR,
                {"application_id": application_id, "phase": "deactivate", "error": repr(exc)},
            )
            raise
        result = self._transition(application_id, to_state="inactive", note="deactivated")
        await self._publish(
            evt.APPLICATION_DEACTIVATED,
            {"application_id": application_id},
        )
        return result

    # ------------------------------------------------------------------ #
    # Workspace integration
    # ------------------------------------------------------------------ #
    async def notify_workspace_focus(
        self, workspace_id: uuid.UUID, application_id: str, focused: bool
    ) -> None:
        """Notify an application that a workspace gained or lost focus.

        The framework validates that the workspace exists (via
        `WorkspaceAccessor`) when one is wired, then forwards the
        event to the application's `on_workspace_focus` method."""
        if self._workspace_manager is not None:
            ws = self._workspace_manager.get_workspace(workspace_id)
            if ws is None:
                raise UnknownApplicationError(
                    f"workspace {workspace_id!s} is not registered"
                )
        app = self._require(application_id)
        await app.on_workspace_focus(workspace_id, focused)

    async def set_current_application_in_workspace(
        self, workspace_id: uuid.UUID, application_id: str
    ) -> None:
        """Convenience: ask the Workspace Manager to focus the given
        application in the given workspace. Used by activation flows
        that want to focus the workspace as a side effect of
        activating an app. No-op when no Workspace Manager is wired."""
        if self._workspace_manager is None:
            return None
        if application_id not in self._apps:
            raise UnknownApplicationError(application_id)
        await self._workspace_manager.set_current_application(
            workspace_id, application_id
        )

    # ------------------------------------------------------------------ #
    # Routing
    # ------------------------------------------------------------------ #
    async def route(self, request) -> None:
        """Dispatch a `RoutingRequest` to the target application."""
        target = self._require(request.target_application_id)
        await target.handle_routing(request)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _require(self, application_id: str) -> ApplicationProtocol:
        if application_id not in self._apps:
            raise UnknownApplicationError(application_id)
        return self._apps[application_id]

    def _transition(
        self,
        application_id: str,
        *,
        to_state: LifecycleState,
        note: str | None = None,
        seed_metadata: Application | None = None,
    ) -> Application:
        previous = self._states.get(application_id)
        from_state: LifecycleState = (
            previous.lifecycle_state if previous is not None else "unregistered"
        )
        return self._transition_sync(
            application_id,
            from_state=from_state,
            to_state=to_state,
            note=note,
            seed_metadata=seed_metadata,
        )

    def _transition_sync(
        self,
        application_id: str,
        *,
        from_state: LifecycleState,
        to_state: LifecycleState,
        note: str | None = None,
        seed_metadata: Application | None = None,
    ) -> Application:
        allowed = _ALLOWED_TRANSITIONS.get(from_state, set())
        if to_state not in allowed and from_state != to_state:
            raise ApplicationLifecycleError(
                application_id,
                from_state,
                to_state,
                reason=f"allowed transitions from {from_state!r}: {sorted(allowed)}",
            )
        previous = self._states.get(application_id)
        if previous is None:
            if seed_metadata is None:
                raise ApplicationLifecycleError(
                    application_id,
                    from_state,
                    to_state,
                    reason="no seed metadata and no prior state",
                )
            base = seed_metadata
        else:
            base = previous
        new_state = base.model_copy(
            update={
                "lifecycle_state": to_state,
                "last_transition_at": datetime.now(timezone.utc),
                "last_error": (
                    note if to_state == "error"
                    else None if from_state == "error"
                    else base.last_error
                ),
            }
        )
        self._states[application_id] = new_state
        event = LifecycleEvent(
            application_id=application_id,
            from_state=from_state,
            to_state=to_state,
            note=note,
        )
        self._history.setdefault(application_id, deque(maxlen=self._history_size)).append(event)
        self._event_ring.append(event)
        return new_state

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


__all__ = [
    "ApplicationFramework",
    "BaseApplication",
    "SOURCE_MODULE",
]