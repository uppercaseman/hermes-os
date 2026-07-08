"""Pydantic data contracts for the Notification Center."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["info", "success", "warning", "error", "critical"]


class Notification(BaseModel):
    """One notification. Identity, severity, title, body, source
    module, event_type that triggered it (when sourced from the
    bus), read / dismissed flags, and timestamp."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    severity: Severity
    title: str
    body: str = ""
    source_module: str | None = None
    source_event_type: str | None = None
    correlation_id: uuid.UUID | None = None
    is_read: bool = False
    is_dismissed: bool = False
    created_at: datetime


class NotificationAggregate(BaseModel):
    """A summary rollup of notification state: counts per severity
    plus unread total. Computed on demand."""

    total: int
    unread: int
    by_severity: dict[str, int] = Field(default_factory=dict)
    unread_by_severity: dict[str, int] = Field(default_factory=dict)


__all__ = ["Notification", "NotificationAggregate", "Severity"]