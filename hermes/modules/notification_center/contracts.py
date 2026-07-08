"""Notification Center Protocol contracts.

- `EventSource` -- narrow Protocol re-declaring just the surface
  the Center needs from the EventBus. The real bus satisfies it
  implicitly; tests pass a stub with the same shape.

- `NotificationSink` -- the consumer-facing surface. A future UI
  binds against this Protocol rather than the concrete class.

- `NotificationCenterProtocol` -- the full surface.
"""
from __future__ import annotations

import uuid
from typing import Awaitable, Callable, Protocol, runtime_checkable

from hermes.core.event_bus.models import Event
from hermes.modules.notification_center.models import (
    Notification,
    NotificationAggregate,
    Severity,
)


@runtime_checkable
class EventSource(Protocol):
    """Subset of EventBus the Notification Center depends on."""

    async def subscribe(
        self,
        event_type: str,
        handler: Callable[[Event], Awaitable[None]],
    ) -> None:
        ...


@runtime_checkable
class NotificationSink(Protocol):
    """The consumer-facing surface. A UI binds against this so the
    UI never imports the Center's concrete class."""

    def list_notifications(
        self,
        *,
        severity: Severity | None = None,
        unread_only: bool = False,
    ) -> list[Notification]:
        ...

    def unread_count(self, *, severity: Severity | None = None) -> int:
        ...

    def aggregate(self) -> NotificationAggregate:
        ...

    def mark_read(self, notification_id: uuid.UUID) -> Notification:
        ...

    def dismiss(self, notification_id: uuid.UUID) -> Notification:
        ...

    def clear(self, *, severity: Severity | None = None) -> int:
        ...


@runtime_checkable
class NotificationCenterProtocol(Protocol):
    def raise_notification(
        self,
        *,
        severity: Severity,
        title: str,
        body: str = "",
        source_module: str | None = None,
        correlation_id: uuid.UUID | None = None,
    ) -> Notification:
        ...

    def list_notifications(
        self,
        *,
        severity: Severity | None = None,
        unread_only: bool = False,
    ) -> list[Notification]:
        ...

    def unread_count(self, *, severity: Severity | None = None) -> int:
        ...

    def aggregate(self) -> NotificationAggregate:
        ...

    def mark_read(self, notification_id: uuid.UUID) -> Notification:
        ...

    def dismiss(self, notification_id: uuid.UUID) -> Notification:
        ...

    def clear(self, *, severity: Severity | None = None) -> int:
        ...

    def register_severity_rule(
        self, event_type_prefix: str, severity: Severity
    ) -> None:
        ...


__all__ = [
    "EventSource",
    "NotificationCenterProtocol",
    "NotificationSink",
]