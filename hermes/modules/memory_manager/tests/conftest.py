import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.memory_manager.interface import build_memory_manager


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def memory():
    """A standalone manager: no backend, no vector search, no event
    bus -- pure in-process usage."""
    return build_memory_manager()
