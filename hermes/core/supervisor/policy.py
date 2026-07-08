"""Retry-policy primitives.

Used by Commander to decide whether, and how long to wait before, retrying
a failed task. Kept separate from Commander itself because the same policy
shape (max attempts + exponential backoff) is what a future module
supervisor would use to restart a crashed module -- this is the one
building block, not two.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=3, ge=1)
    backoff_base_seconds: float = Field(default=1.0, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1)

    def should_retry(self, attempt: int, max_attempts: int | None = None) -> bool:
        """`attempt` is the attempt number that just failed (1-indexed)."""
        limit = max_attempts if max_attempts is not None else self.max_attempts
        return attempt < limit

    def next_backoff(self, attempt: int) -> float:
        """Exponential backoff: base * multiplier ** (attempt - 1)."""
        return self.backoff_base_seconds * (self.backoff_multiplier ** (attempt - 1))
