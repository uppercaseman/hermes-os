import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.mission_system.interface import build_mission_system, build_team_builder
from hermes.modules.mission_system.tests.fakes import FakeCommander, FakeMemoryPermissionGranter


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def fake_memory() -> FakeMemoryPermissionGranter:
    return FakeMemoryPermissionGranter()


@pytest.fixture
def team_builder(fake_memory):
    return build_team_builder(memory_manager=fake_memory)


@pytest.fixture
def fake_commander():
    return FakeCommander()


@pytest.fixture
def mission_system(fake_commander, team_builder):
    """A MissionSystem with a scripted Commander and a real team-builder
    (backed by a fake memory granter) -- no event bus, no IntentRouter,
    unless a test adds one explicitly."""
    return build_mission_system(commander=fake_commander, team_builder=team_builder)
