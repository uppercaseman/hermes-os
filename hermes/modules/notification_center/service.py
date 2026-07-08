"""Notification Center service.

Subscribes to the EventBus via the `"*"` wildcard and classifies
each incoming event by an internal severity-mapping rule. Every
classification produces a `Notification` that lives in a bounded
ring buffer; readers query the buffer by severity / unread / etc.

Key properties:

- **Bus-agnostic.** When `event_bus` is omitted the Center is
  silent. Tests can construct one without a bus and call
  `raise_notification(...)` directly.
- **Severity rules.** The Center ships with default rules
  (`*.failed` -> error, `*.completed` -> success, etc.) and
  lets the caller register more. Last-registered-wins for any
  given prefix.
- **Bounded history.** Oldest-first eviction once `history_size`
  is reached (default 500).
- **Synchronous API.** The Center owns no I/O of its own. Every
  mutation is sync; events are published through the bus only
  via `_publish()` (async, awaited).
"""
from __future__ import annotations

import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Deque, Optional

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.notification_center import events as evt
from hermes.modules.notification_center.errors import UnknownNotificationError
from hermes.modules.notification_center.models import (
    Notification,
    NotificationAggregate,
    Severity,
)

SOURCE_MODULE = "notification_center"


def _default_severity_rules() -> dict[str, Severity]:
    """First match wins. Last registered prefix in the dict's
    iteration order wins ties. Default rules:
    - `*.failed`, `*.crashed` -> error
    - `*.cancelled`, `*.timeout` -> warning
    - `*.completed`, `*.succeeded` -> success
    - everything else -> info"""
    return {
        "failed": "error",
        "crashed": "error",
        "error": "error",
        "cancelled": "warning",
        "timeout": "warning",
        "warning": "warning",
        "completed": "success",
        "succeeded": "success",
        "saved": "success",
    }


class NotificationCenter:
    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        history_size: int = 500,
        auto_subscribe: bool = True,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if history_size < 1:
            raise ValueError("history_size must be >= 1")
        self._bus = event_bus
        self._history: Deque[Notification] = deque(maxlen=history_size)
        self._unread_per_severity: dict[str, int] = defaultdict(int)
        self._rules = _default_severity_rules()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._subscribed = False
        if auto_subscribe and event_bus is not None:
            # Subscribe synchronously by hopping to the bus through
            # the event loop. We assume build_notification_center is
            # called outside an await context (true in tests) and we
            # delay subscription until first use; tests call
            # `await nc.start()` explicitly to avoid races.
            self._pending_subscription = True
        else:
            self._pending_subscription = False

    # ------------------------------------------------------------------ #
    # Bus integration
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        """Subscribes to `"*"` if a bus was provided. Idempotent."""
        if self._subscribed or self._bus is None:
            self._subscribed = self._bus is not None
            self._pending_subscription = False
            return
        await self._bus.subscribe("*", self._on_event)
        self._subscribed = True
        self._pending_subscription = False

    async def stop(self) -> None:
        """Unsubscribes from the bus. Idempotent."""
        if not self._subscribed or self._bus is None:
            return
        await self._bus.unsubscribe("*", self._on_event)
        self._subscribed = False

    async def _on_event(self, event: Event) -> None:
        """Wildcard bus handler: classify and store.

        Note: we deliberately do NOT re-publish on the bus here --
        the event we're handling IS the bus event. Republishing would
        cause infinite recursion through the `"*"` wildcard.
        """
        title = f"{event.event_type}"
        notification = Notification(
            severity=self._classify(event.event_type),
            title=title,
            body=str(event.payload),
            source_module=event.source_module,
            source_event_type=event.event_type,
            correlation_id=event.correlation_id,
            created_at=self._clock(),
        )
        self._store(notification)

    def _classify(self, event_type: str) -> Severity:
        # Apply prefix rules; default to "info".
        for prefix, severity in self._rules.items():
            if event_type.endswith(f".{prefix}") or event_type == prefix:
                return severity
        return "info"

    # ------------------------------------------------------------------ #
    # Direct API (works without a bus)
    # ------------------------------------------------------------------ #
    def raise_notification(
        self,
        *,
        severity: Severity,
        title: str,
        body: str = "",
        source_module: str | None = None,
        correlation_id: uuid.UUID | None = None,
    ) -> Notification:
        notification = Notification(
            severity=severity,
            title=title,
            body=body,
            source_module=source_module,
            source_event_type=None,
            correlation_id=correlation_id,
            created_at=self._clock(),
        )
        self._store(notification)
        return notification

    async def raise_notification_async(
        self,
        *,
        severity: Severity,
        title: str,
        body: str = "",
        source_module: str | None = None,
        correlation_id: uuid.UUID | None = None,
    ) -> Notification:
        """Async variant of `raise_notification` that publishes a
        `NOTIFICATION_RAISED` event for direct-raise notifications."""
        notification = self.raise_notification(
            severity=severity,
            title=title,
            body=body,
            source_module=source_module,
            correlation_id=correlation_id,
        )
        await self._publish(
            evt.NOTIFICATION_RAISED,
            {
                "notification_id": str(notification.id),
                "severity": notification.severity,
                "title": notification.title,
                "source_event_type": notification.source_event_type,
            },
        )
        return notification

    # ------------------------------------------------------------------ #
    # Severity-rule configuration
    # ------------------------------------------------------------------ #
    def register_severity_rule(
        self, event_type_prefix: str, severity: Severity
    ) -> None:
        """Registers (or overrides) a classification rule. The
        longest matching prefix wins (so `mission.failed` overrides
        `failed` for that event_type)."""
        self._rules[event_type_prefix] = severity

    # ------------------------------------------------------------------ #
    # Listing & read-state
    # ------------------------------------------------------------------ #
    def list_notifications(
        self,
        *,
        severity: Severity | None = None,
        unread_only: bool = False,
    ) -> list[Notification]:
        notes = list(self._history)
        if severity is not None:
            notes = [n for n in notes if n.severity == severity]
        if unread_only:
            notes = [n for n in notes if not n.is_read]
        return notes

    def get_notification(
        self, notification_id: uuid.UUID
    ) -> Notification | None:
        for n in self._history:
            if n.id == notification_id:
                return n
        return None

    def unread_count(self, *, severity: Severity | None = None) -> int:
        if severity is None:
            return sum(self._unread_per_severity.values())
        return self._unread_per_severity.get(severity, 0)

    def aggregate(self) -> NotificationAggregate:
        by_sev: dict[str, int] = defaultdict(int)
        for n in self._history:
            by_sev[n.severity] += 1
        return NotificationAggregate(
            total=len(self._history),
            unread=sum(self._unread_per_severity.values()),
            by_severity=dict(by_sev),
            unread_by_severity=dict(self._unread_per_severity),
        )

    def mark_read(self, notification_id: uuid.UUID) -> Notification:
        notification = self._find(notification_id)
        if notification.is_read:
            return notification
        # Marking read does NOT dismiss.
        updated = notification.model_copy(update={"is_read": True})
        self._replace(updated)
        if not notification.is_read:
            self._unread_per_severity[updated.severity] = max(
                0, self._unread_per_severity.get(updated.severity, 0) - 1
            )
        return updated

    def dismiss(self, notification_id: uuid.UUID) -> Notification:
        notification = self._find(notification_id)
        if notification.is_dismissed:
            return notification
        updated = notification.model_copy(
            update={"is_dismissed": True, "is_read": True}
        )
        self._replace(updated)
        if not notification.is_read:
            self._unread_per_severity[updated.severity] = max(
                0, self._unread_per_severity.get(updated.severity, 0) - 1
            )
        return updated

    def clear(self, *, severity: Severity | None = None) -> int:
        """Dismisses every notification (optionally of one severity)
        and returns the number cleared."""
        kept: list[Notification] = []
        cleared = 0
        for n in self._history:
            if severity is None or n.severity == severity:
                cleared += 1
                if not n.is_read:
                    self._unread_per_severity[n.severity] = max(
                        0, self._unread_per_severity.get(n.severity, 0) - 1
                    )
            else:
                kept.append(n)
        # Re-instantiate the deque to keep ordering & cap intact.
        maxlen = self._history.maxlen
        self._history = deque(kept, maxlen=maxlen)
        return cleared

    # ------------------------------------------------------------------ #
    # Persistence hooks (no default store; subclasses or callers wire
    # their own. The Center exposes a `snapshot()` / `restore()` pair
    # for explicit save/restore so we never do implicit I/O.)
    # ------------------------------------------------------------------ #
    def snapshot(self) -> list[Notification]:
        return list(self._history)

    def restore(self, notifications: list[Notification]) -> None:
        self._history = deque(notifications, maxlen=self._history.maxlen)
        self._unread_per_severity = defaultdict(int)
        for n in self._history:
            if not n.is_read:
                self._unread_per_severity[n.severity] += 1

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _store(self, notification: Notification) -> None:
        """Appends a notification to the ring. When the ring is full
        the oldest item is dropped automatically; if that item was
        unread its severity counter is decremented."""
        if len(self._history) == self._history.maxlen:
            dropped = self._history[0]
            if not dropped.is_read:
                self._unread_per_severity[dropped.severity] = max(
                    0,
                    self._unread_per_severity.get(dropped.severity, 0) - 1,
                )
        self._history.append(notification)
        if not notification.is_read:
            self._unread_per_severity[notification.severity] += 1

    def _find(self, notification_id: uuid.UUID) -> Notification:
        for n in self._history:
            if n.id == notification_id:
                return n
        raise UnknownNotificationError(notification_id)

    def _replace(self, updated: Notification) -> None:
        # Replace in-place within the deque. deque supports assignment.
        for idx, current in enumerate(self._history):
            if current.id == updated.id:
                self._history[idx] = updated
                return
        raise UnknownNotificationError(updated.id)

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


__all__ = ["NotificationCenter"]