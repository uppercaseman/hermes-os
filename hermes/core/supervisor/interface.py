"""Public entry point for the Supervisor.

Everything outside this module -- Commander's future boot sequence, CLI,
tests -- imports from here, never from service.py directly. Mirrors
Commander's own interface.py convention.
"""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.core.supervisor.contracts import Supervisable
from hermes.core.supervisor.models import SupervisedUnitConfig, UnitStatus
from hermes.core.supervisor.policy import RetryPolicy
from hermes.core.supervisor.service import Supervisor

__all__ = [
    "Supervisor",
    "Supervisable",
    "SupervisedUnitConfig",
    "UnitStatus",
    "RetryPolicy",
    "build_supervisor",
]


def build_supervisor(*, event_bus: EventBus) -> Supervisor:
    """Constructs a Supervisor bound to the given event bus.

    Register `Supervisable` units on the returned instance with
    `register()`, then call `start_all()`.
    """
    return Supervisor(event_bus=event_bus)
