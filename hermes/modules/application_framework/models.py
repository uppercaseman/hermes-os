"""Pydantic data contracts for Application Framework.

Distinct from `application_registry.models.Application` (which is
metadata-only -- a catalog record). The `Application` Pydantic
model defined here is the **runtime contract**: it carries the
identity, lifecycle state, declared capabilities / permissions /
event subscriptions, routing context, and workspace integration
metadata that the Framework tracks for every running app.

`LifecycleState` is a closed Literal mirroring the state machine:
`unregistered -> registered -> starting -> active <-> inactive ->
stopped`, with `error` as a terminal failure state.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


LifecycleState = Literal[
    "unregistered",
    "registered",
    "starting",
    "active",
    "inactive",
    "stopped",
    "error",
]


Permission = Literal[
    "workspace.read",
    "workspace.write",
    "memory.read",
    "memory.write",
    "mission.read",
    "mission.write",
    "session.read",
    "session.write",
    "notification.read",
    "notification.write",
    "event.subscribe",
    "event.publish",
    "provider.read",
    "provider.write",
    "knowledge.read",
    "knowledge.write",
]


EventSubscription = Literal[
    "mission.*",
    "workspace.*",
    "session.*",
    "memory.*",
    "notification.*",
    "application.*",
    "application_framework.*",
    "provider.*",
    "knowledge.*",
    "*",
]


class RoutingRequest(BaseModel):
    """A request for the framework to route an inbound event or
    user action to the appropriate application instance.

    `source` is the producer (e.g. `'workspace_manager'`,
    `'mission_control'`); `target_application_id` is the resolved
    destination; `kind` is one of `'event'`, `'action'`, `'route'`;
    `payload` is the arbitrary JSON-safe blob."""

    source: str
    target_application_id: str
    kind: Literal["event", "action", "route"]
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: uuid.UUID = Field(default_factory=uuid.uuid4)


class WorkspaceIntegration(BaseModel):
    """The workspace-integration metadata an `Application` declares:
    its `route` (URL fragment), its window title pattern, the focus
    events it cares about, and the `workspace_ids` it is registered
    against (empty list means all workspaces)."""

    route: str
    window_title_pattern: str = ""
    focus_events: list[str] = Field(default_factory=list)
    workspace_ids: list[uuid.UUID] = Field(default_factory=list)


class Application(BaseModel):
    """The runtime contract every Hermes application conforms to.

    Identity (`id`, `name`, `version`, `category`) flows from the
    Application Registry catalog but is **denormalised here** so the
    Framework can answer questions without round-tripping the bus.

    Lifecycle state (`lifecycle_state`, `last_transition_at`,
    `last_error`) is **owned by the Framework** and mutated only
    through the Framework's API.

    Static metadata (`required_capabilities`, `required_permissions`,
    `event_subscriptions`, `routing`, `workspace_integration`) is
    declared by the application at registration time and is
    immutable thereafter.
    """

    id: str = Field(min_length=1, max_length=64)
    name: str
    version: str = "0.0.0"
    category: str = "custom"
    description: str = ""

    lifecycle_state: LifecycleState = "registered"
    last_transition_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_error: str | None = None

    required_capabilities: list[str] = Field(default_factory=list)
    required_permissions: list[Permission] = Field(default_factory=list)
    event_subscriptions: list[EventSubscription] = Field(default_factory=list)
    routing: RoutingRequest | None = None
    workspace_integration: WorkspaceIntegration | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class LifecycleEvent(BaseModel):
    """A record of one lifecycle transition. Stored in the
    Framework's bounded event ring so a UI or audit consumer can
    reconstruct the transition history of any application."""

    application_id: str
    from_state: LifecycleState
    to_state: LifecycleState
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    note: str | None = None


__all__ = [
    "Application",
    "LifecycleEvent",
    "LifecycleState",
    "EventSubscription",
    "Permission",
    "RoutingRequest",
    "WorkspaceIntegration",
]