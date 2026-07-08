"""Pydantic data contracts for the Logging System."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["debug", "info", "warn", "error"]


class LogEntry(BaseModel):
    """One captured event, structured and redacted at capture time.

    `mission_id`/`workflow_run_id`/`task_id`/`tool_name` are derived once
    when the entry is captured (from the event's payload), not
    recomputed per query -- see service.py's `capture()`.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: str
    source_module: str
    correlation_id: uuid.UUID
    severity: Severity
    payload: dict[str, Any] = Field(default_factory=dict)

    mission_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    tool_name: str | None = None

    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
