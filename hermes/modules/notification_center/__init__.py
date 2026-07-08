"""Notification Center -- aggregated bus-driven notifications.

Subscribes to the EventBus via the `"*"` wildcard, classifies each
incoming event by an internal severity-mapping rule, and stores
the result in a bounded ring buffer. The Center exposes APIs to
list notifications, count unread, mark read, dismiss, and clear.

The Center does NOT subscribe only to specific events. Doing so
would tie its concrete behavior to whichever event types it
happens to subscribe to -- instead, the Center subscribes to
everything and applies an internal filter that the caller can
configure via `register_severity_rule(event_type_prefix, severity)`.
"""
from hermes.modules.notification_center.interface import (
    Severity,
    build_notification_center,
)
from hermes.modules.notification_center.service import NotificationCenter

__all__ = ["NotificationCenter", "Severity", "build_notification_center"]