import asyncio
import time

from hermes.modules.tool_manager.models import RateLimitPolicy
from hermes.modules.tool_manager.rate_limiter import RateLimiter


class FakeClock:
    """A controllable clock for deterministic refill-math tests -- no
    real waiting required."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_starts_with_a_full_bucket():
    limiter = RateLimiter(RateLimitPolicy(max_calls=5, per_seconds=10.0), clock=FakeClock())

    assert limiter.available_tokens == 5


def test_refills_proportionally_to_elapsed_time():
    clock = FakeClock()
    limiter = RateLimiter(RateLimitPolicy(max_calls=10, per_seconds=10.0), clock=clock)
    limiter._tokens = 0.0  # simulate having just exhausted the bucket

    clock.advance(5.0)  # half the window elapsed -> half the bucket refills

    assert limiter.available_tokens == 5.0


def test_never_refills_past_max_calls():
    clock = FakeClock()
    limiter = RateLimiter(RateLimitPolicy(max_calls=3, per_seconds=1.0), clock=clock)

    clock.advance(100.0)

    assert limiter.available_tokens == 3


async def test_acquire_does_not_block_while_tokens_available():
    limiter = RateLimiter(RateLimitPolicy(max_calls=5, per_seconds=10.0), clock=FakeClock())

    await asyncio.wait_for(limiter.acquire(), timeout=0.05)

    assert limiter.available_tokens == 4


async def test_acquire_blocks_until_a_token_refills():
    """Real-timing integration check: with a 2-per-0.05s bucket, the 3rd
    immediate acquire() must actually wait for a refill rather than
    returning instantly."""
    limiter = RateLimiter(RateLimitPolicy(max_calls=2, per_seconds=0.05))

    start = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()  # bucket exhausted -- must wait for a refill
    elapsed = time.monotonic() - start

    assert elapsed > 0.01  # meaningfully more than an instant return
