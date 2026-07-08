"""Pydantic data contracts for the Task Queue."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from hermes.core.supervisor.policy import RetryPolicy

TaskStatus = Literal["queued", "claimed", "completed", "failed", "dead_letter"]


class QueuedTask(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    kind: str = "generic"
    payload: dict[str, Any] = Field(default_factory=dict)
    status: TaskStatus = "queued"

    priority: int = Field(default=100, ge=0, description="Lower is claimed first.")
    scheduled_for: datetime | None = Field(default=None, description="Not claimable before this time.")
    depends_on: list[uuid.UUID] = Field(default_factory=list)
    idempotency_key: str | None = None

    mission_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None
    correlation_id: uuid.UUID = Field(default_factory=uuid.uuid4)

    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    attempts: int = 0
    claim_attempts: int = Field(default=0, description="Crash-recovery reclaim count, distinct from `attempts`.")

    claimed_by: str | None = None
    claimed_at: datetime | None = None
    visible_at: datetime | None = Field(default=None, description="Visibility timeout for crash recovery.")

    output: dict[str, Any] | None = None
    error: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskExecutionResult(BaseModel):
    """What a `TaskExecutor` returns after attempting one task."""

    status: Literal["completed", "failed"]
    output: dict[str, Any] | None = None
    error: str | None = None
