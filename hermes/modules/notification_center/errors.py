"""Notification Center-specific exception types."""
from __future__ import annotations

import uuid


class NotificationCenterError(Exception):
    """Base for notification-level errors."""


class UnknownNotificationError(NotificationCenterError):
    def __init__(self, notification_id: uuid.UUID) -> None:
        self.notification_id = notification_id
        super().__init__(
            f"notification {notification_id!s} is not in history"
        )


class NotificationCenterConfigError(NotificationCenterError):
    """Construction-time configuration error."""


__all__ = [
    "NotificationCenterError",
    "UnknownNotificationError",
    "NotificationCenterConfigError",
]