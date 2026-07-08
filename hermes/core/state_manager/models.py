"""Pydantic data contracts for the State Manager."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

ModuleState = Literal["healthy", "busy", "idle", "offline", "restarting", "failed", "degraded"]


class Heartbeat(BaseModel):
    """The most recently known state for one module -- either actively
    self-reported (`report_heartbeat`) or passively derived from a
    Supervisor lifecycle event. See service.py for why that distinction
    matters for staleness detection."""

    module_name: str
    state: ModuleState
    reported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    detail: str | None = None


class RestartRequest(BaseModel):
    """One request to restart a module -- whether raised automatically
    (heartbeat timeout, exhausted Supervisor retries) or on demand."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    module_name: str
    reason: str | None = None
    requested_by: str = "unknown"
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: Literal["pending", "completed", "failed"] = "pending"


class ModuleDiagnostics(BaseModel):
    """Everything known about one module, for diagnostic reporting."""

    module_name: str
    reported_state: ModuleState
    effective_state: ModuleState
    last_heartbeat_at: datetime | None
    heartbeat_stale: bool
    dependencies: list[str] = Field(default_factory=list)
    unmet_dependencies: list[str] = Field(default_factory=list)
    restart_count: int = 0
    last_restart_reason: str | None = None


class SystemDiagnostics(BaseModel):
    """A dashboard-ready snapshot of every tracked module. Plain,
    JSON-serializable pydantic data is the entire "future dashboard
    support" hook: a future HTTP endpoint serves `.model_dump()` /
    `.model_dump_json()` of this directly."""

    generated_at: datetime
    modules: list[ModuleDiagnostics]
    overall_state: Literal["healthy", "degraded", "critical"]
