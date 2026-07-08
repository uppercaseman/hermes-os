import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()
