import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.core.state_manager.interface import build_state_manager
from hermes.core.supervisor.interface import build_supervisor


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def state_manager():
    """A standalone manager: no event bus, no Supervisor -- pure
    heartbeat/declaration-driven usage."""
    return build_state_manager()
