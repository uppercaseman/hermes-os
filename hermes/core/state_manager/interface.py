"""Public entry point for the State Manager.

Everything outside this module -- Commander, CLI, tests -- imports from
here, never from service.py directly. Mirrors every other module's
interface.py convention.
"""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.core.state_manager.errors import UnknownModuleError
from hermes.core.state_manager.models import (
    Heartbeat,
    ModuleDiagnostics,
    ModuleState,
    RestartRequest,
    SystemDiagnostics,
)
from hermes.core.state_manager.service import StateManager
from hermes.core.supervisor.interface import Supervisor
from hermes.core.supervisor.policy import RetryPolicy

__all__ = [
    "StateManager",
    "ModuleState",
    "Heartbeat",
    "RestartRequest",
    "ModuleDiagnostics",
    "SystemDiagnostics",
    "UnknownModuleError",
    "build_state_manager",
]


def build_state_manager(
    *,
    event_bus: EventBus | None = None,
    supervisor: Supervisor | None = None,
    heartbeat_timeout_seconds: float = 30.0,
    sweep_interval_seconds: float = 10.0,
    recovery_policy: RetryPolicy | None = None,
) -> StateManager:
    """Constructs a State Manager. Call `start()` on the result to begin
    the heartbeat-staleness sweep and (if `event_bus` was given)
    automatic state tracking from Supervisor lifecycle events."""
    return StateManager(
        event_bus=event_bus,
        supervisor=supervisor,
        heartbeat_timeout_seconds=heartbeat_timeout_seconds,
        sweep_interval_seconds=sweep_interval_seconds,
        recovery_policy=recovery_policy,
    )
