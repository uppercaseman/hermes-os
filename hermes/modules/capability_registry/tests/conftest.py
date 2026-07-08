import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.capability_registry.interface import build_capability_registry


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def registry():
    return build_capability_registry()


@pytest.fixture
def wired_registry(bus):
    """A registry with an event bus, already started -- for tests of
    automatic health tracking from Supervisor events."""
    return build_capability_registry(event_bus=bus)
