"""Notification Center event vocabulary."""

NOTIFICATION_RAISED = "notification_center.notification.raised"
NOTIFICATION_READ = "notification_center.notification.read"
NOTIFICATION_DISMISSED = "notification_center.notification.dismissed"
NOTIFICATION_CLEARED = "notification_center.notification.cleared"
FILTER_CHANGED = "notification_center.filter.changed"

__all__ = [
    "NOTIFICATION_RAISED",
    "NOTIFICATION_READ",
    "NOTIFICATION_DISMISSED",
    "NOTIFICATION_CLEARED",
    "FILTER_CHANGED",
]