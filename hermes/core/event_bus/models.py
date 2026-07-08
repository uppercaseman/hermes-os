"""The single envelope type that crosses the bus.

Every module -- Commander included -- only ever exchanges `Event` objects.
No module imports another module's internal types across a process
boundary; the `payload` dict plus `event_type` is the entire contract.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

EventLevel = Literal["debug", "info", "warn", "error"]


class Event(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: str
    source_module: str
    correlation_id: uuid.UUID
    payload: dict[str, Any] = Field(default_factory=dict)
    level: EventLevel = "info"
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
