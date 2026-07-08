import uuid

import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.core.event_bus.models import Event
from hermes.modules.logging_system.interface import build_logging_system


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def logging_system():
    """A standalone logger: no event bus -- entries fed via `capture()`
    directly, which every test can do regardless of bus wiring."""
    return build_logging_system()


def make_event(event_type: str, *, source_module: str = "test", payload: dict | None = None, level: str = "info", correlation_id=None) -> Event:
    return Event(
        event_type=event_type,
        source_module=source_module,
        correlation_id=correlation_id or uuid.uuid4(),
        payload=payload or {},
        level=level,
    )
