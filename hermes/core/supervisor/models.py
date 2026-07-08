"""Data contracts for the Supervisor."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from hermes.core.supervisor.policy import RetryPolicy

RestartStrategy = Literal["permanent", "transient", "temporary"]
"""Erlang-style restart semantics, adapted to event-driven modules (see
contracts.py -- there is no "normal exit" for a `start()` that returns
successfully, only a healthy running module):

- "permanent": always restart on any failure (a crash, or `health_check`
  reporting unhealthy), until the retry policy's attempts are exhausted.
- "transient": restart only on a crash (an exception from `start` or
  `health_check`), not when `health_check` merely returns False.
- "temporary": never restart automatically, regardless of the failure.

None of these ever apply to a deliberate `Supervisor.stop()` -- that never
triggers a restart.
"""

UnitState = Literal["starting", "running", "restarting", "stopped", "failed"]


class SupervisedUnitConfig(BaseModel):
    """How the Supervisor should manage one registered unit."""

    name: str
    restart_strategy: RestartStrategy = "permanent"
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    health_check_interval_seconds: float = Field(default=5.0, gt=0)


class UnitStatus(BaseModel):
    """Point-in-time observable state of one supervised unit."""

    name: str
    state: UnitState
    restart_count: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
