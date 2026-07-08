import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.core.supervisor.policy import RetryPolicy
from hermes.modules.tool_manager.interface import build_tool_manager
from hermes.modules.tool_manager.models import RateLimitPolicy, ToolAdapterConfig

INSTANT_RETRY = RetryPolicy(max_attempts=3, backoff_base_seconds=0, backoff_multiplier=1)
FAST_HEALTH_CHECK = 0.02
GENEROUS_RATE_LIMIT = RateLimitPolicy(max_calls=1000, per_seconds=1.0)


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def tool_manager(bus):
    return build_tool_manager(event_bus=bus)


def fast_config(name: str, **overrides) -> ToolAdapterConfig:
    """A ToolAdapterConfig tuned for fast, deterministic tests: instant
    retry backoff, a generous rate limit, and a quick health-check
    interval."""
    kwargs = dict(
        name=name,
        retry_policy=INSTANT_RETRY,
        rate_limit=GENEROUS_RATE_LIMIT,
        health_check_interval_seconds=FAST_HEALTH_CHECK,
        invocation_timeout_seconds=5.0,
    )
    kwargs.update(overrides)
    return ToolAdapterConfig(**kwargs)
