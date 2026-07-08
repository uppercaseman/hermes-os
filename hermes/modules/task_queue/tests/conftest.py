import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.core.supervisor.policy import RetryPolicy
from hermes.modules.task_queue.interface import build_task_queue

INSTANT_RETRY = RetryPolicy(max_attempts=3, backoff_base_seconds=0, backoff_multiplier=1)


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def queue():
    """A standalone queue: in-memory backend, no event bus, a short
    visibility timeout so crash-recovery tests run fast."""
    return build_task_queue(visibility_timeout_seconds=0.05, max_claim_attempts=2)
