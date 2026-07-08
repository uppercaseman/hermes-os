import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.core.supervisor.policy import RetryPolicy
from hermes.modules.workflow_engine.interface import build_workflow_engine

INSTANT_RETRY = RetryPolicy(max_attempts=3, backoff_base_seconds=0, backoff_multiplier=1)


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def engine():
    """A standalone engine: no bus, no Tool Manager, no Memory Manager,
    no Capability Registry -- fine for noop-only workflows."""
    return build_workflow_engine()
