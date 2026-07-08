import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.configuration_manager.interface import build_configuration_manager


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def config_manager():
    """A standalone manager: no event bus, no config file -- only
    whatever HERMES_* variables happen to be in this process's real
    environment. Tests that care about env values set and restore them
    directly via `os.environ`, rather than depending on a `monkeypatch`
    fixture this environment's manual test harness doesn't provide."""
    return build_configuration_manager()
