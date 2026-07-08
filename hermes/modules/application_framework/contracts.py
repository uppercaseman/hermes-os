"""Application Framework Protocol contracts.

Defines the surfaces every other module binds against:

- `ApplicationProtocol` -- the **ten-verb** interface every Hermes
  application implements. Per the directive, all ten are required
  (no default no-ops): startup, shutdown, activate, deactivate,
  get_metadata, get_required_capabilities, get_required_permissions,
  get_event_subscriptions, get_workspace_route, on_workspace_focus.
- `ApplicationFrameworkProtocol` -- the framework's own surface.
  Workspace Manager and Application Registry bind against this
  Protocol, not against the framework's concrete class.
- `WorkspaceAccessor` and `ApplicationSource` -- the **narrow**
  shapes the framework uses to mediate with Workspace Manager and
  Application Registry. They are re-declared here (not imported
  from the peer modules) so the framework does not import any
  sibling module's concrete class.

`ApplicationProtocol` is `@runtime_checkable` so the framework's
registration API can `isinstance(obj, ApplicationProtocol)` guard
against non-conforming plugins.
"""
from __future__ import annotations

import uuid
from typing import Any, Protocol, runtime_checkable

from hermes.modules.application_framework.models import (
    Application,
    EventSubscription,
    Permission,
    RoutingRequest,
    WorkspaceIntegration,
)


@runtime_checkable
class WorkspaceAccessor(Protocol):
    """The narrow shape Application Framework uses to validate a
    `workspace_id` and (optionally) set the current application
    pointer on a workspace. Re-declared from
    `workspace_manager.WorkspaceAccessor`; this module never imports
    the Workspace Manager's concrete class.
    """

    def get_workspace(self, workspace_id: uuid.UUID) -> Any | None:
        ...

    async def set_current_application(
        self, workspace_id: uuid.UUID, application_id: str
    ) -> Any:
        ...


@runtime_checkable
class ApplicationSource(Protocol):
    """The narrow shape Application Framework uses to read
    catalog metadata for a registered application id. Re-declared
    from `application_registry.ApplicationSource`; this module
    never imports the Registry's concrete class.
    """

    def get_application(self, application_id: str) -> Any | None:
        ...

    def has_application(self, application_id: str) -> bool:
        ...


@runtime_checkable
class ApplicationProtocol(Protocol):
    """The ten-verb contract every Hermes application MUST implement.

    Per the Sprint-5b directive: all ten are required. Implementations
    may be classes, instances, or test doubles; the framework's
    `register_application` performs an `isinstance(obj, ApplicationProtocol)`
    check before accepting the object.
    """

    # --- lifecycle --- #
    async def startup(self) -> None:
        ...

    async def shutdown(self) -> None:
        ...

    async def activate(self) -> None:
        ...

    async def deactivate(self) -> None:
        ...

    # --- static metadata --- #
    def get_metadata(self) -> Application:
        ...

    def get_required_capabilities(self) -> list[str]:
        ...

    def get_required_permissions(self) -> list[Permission]:
        ...

    def get_event_subscriptions(self) -> list[EventSubscription]:
        ...

    # --- workspace integration --- #
    def get_workspace_route(self) -> WorkspaceIntegration:
        ...

    async def on_workspace_focus(
        self, workspace_id: uuid.UUID, focused: bool
    ) -> None:
        ...

    # --- routing --- #
    async def handle_routing(self, request: RoutingRequest) -> None:
        ...


@runtime_checkable
class ApplicationFrameworkProtocol(Protocol):
    """The framework's own surface, used by Workspace Manager,
    Application Registry, and any future plugin host."""

    def register_application(self, application: ApplicationProtocol) -> Application:
        ...

    async def register_application_async(
        self, application: ApplicationProtocol
    ) -> Application:
        ...

    def unregister_application(self, application_id: str) -> Application:
        ...

    async def unregister_application_async(self, application_id: str) -> Application:
        ...

    def get_application(self, application_id: str) -> Application | None:
        ...

    def list_applications(self) -> list[Application]:
        ...

    async def startup_application(self, application_id: str) -> Application:
        ...

    async def shutdown_application(self, application_id: str) -> Application:
        ...

    async def activate_application(self, application_id: str) -> Application:
        ...

    async def deactivate_application(self, application_id: str) -> Application:
        ...

    def recent_events(self, limit: int = 50) -> list[Any]:
        ...

    def lifecycle_history(self, application_id: str) -> list[Any]:
        ...


__all__ = [
    "ApplicationProtocol",
    "ApplicationFrameworkProtocol",
    "WorkspaceAccessor",
    "ApplicationSource",
]