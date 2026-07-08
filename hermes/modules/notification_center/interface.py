"""Public entry point for the Notification Center."""
from __future__ import annotations

from typing import Literal

from hermes.core.event_bus.interface import EventBus
from hermes.modules.notification_center.service import NotificationCenter

Severity = Literal["info", "success", "warning", "error", "critical"]

__all__ = [
    "NotificationCenter",
    "Severity",
    "build_notification_center",
]


def build_notification_center(
    *,
    event_bus: EventBus | None = None,
    history_size: int = 500,
    auto_subscribe: bool = True,
) -> NotificationCenter:
    """Constructs a NotificationCenter.

    `event_bus` is optional -- without one, the Center is silent
    (its filtering / aggregating API is still useful for tests).
    When provided and `auto_subscribe=True` (default), the Center
    immediately subscribes to `"*"` and starts classifying
    incoming events.

    `history_size` bounds the ring buffer of stored notifications.
    """
    return NotificationCenter(
        event_bus=event_bus,
        history_size=history_size,
        auto_subscribe=auto_subscribe,
    )