"""Token-bucket rate limiter.

A generic, provider-agnostic primitive: Tool Manager gives every
registered adapter its own instance built from that adapter's
`RateLimitPolicy`, so no adapter ever needs to implement its own rate
limiting.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable

from hermes.modules.tool_manager.models import RateLimitPolicy


class RateLimiter:
    def __init__(self, policy: RateLimitPolicy, *, clock: Callable[[], float] | None = None) -> None:
        """`clock` defaults to `time.monotonic`; tests may inject a fake,
        controllable clock to assert the refill math deterministically
        without waiting on real time."""
        self._max_calls = policy.max_calls
        self._per_seconds = policy.per_seconds
        self._clock = clock or time.monotonic
        self._tokens = float(policy.max_calls)
        self._updated_at = self._clock()
        self._lock = asyncio.Lock()

    @property
    def available_tokens(self) -> float:
        """Current token count after refilling for elapsed time. Safe to
        read without acquiring -- refilling never blocks."""
        self._refill()
        return self._tokens

    async def acquire(self) -> None:
        """Blocks until one call is permitted under the configured rate
        limit, then consumes one token."""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_seconds = self._seconds_until_next_token()
            await asyncio.sleep(wait_seconds)

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(now - self._updated_at, 0.0)
        refill_rate = self._max_calls / self._per_seconds
        self._tokens = min(self._max_calls, self._tokens + elapsed * refill_rate)
        self._updated_at = now

    def _seconds_until_next_token(self) -> float:
        refill_rate = self._max_calls / self._per_seconds
        missing = max(1 - self._tokens, 0.0)
        return max(missing / refill_rate, 0.001)
