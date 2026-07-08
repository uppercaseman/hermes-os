"""Public entry point for the Tool Manager.

Everything outside this module -- Commander's future wiring, CLI, tests --
imports from here, never from service.py directly. Mirrors Commander's and
the Supervisor's own interface.py convention.
"""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.core.supervisor.interface import Supervisor
from hermes.modules.configuration_manager.interface import ConfigurationManager
from hermes.modules.tool_manager.contracts import ToolAdapter
from hermes.modules.tool_manager.errors import UnknownHandleError, UnsupportedCapabilityError
from hermes.modules.tool_manager.models import (
    AuthConfig,
    RateLimitPolicy,
    ToolAdapterConfig,
    ToolCapabilities,
    ToolInvocationHandle,
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolStatus,
    ToolStreamChunk,
)
from hermes.modules.tool_manager.service import ToolManager

__all__ = [
    "ToolManager",
    "ToolAdapter",
    "ToolAdapterConfig",
    "AuthConfig",
    "RateLimitPolicy",
    "ToolCapabilities",
    "ToolInvocationRequest",
    "ToolInvocationResult",
    "ToolInvocationHandle",
    "ToolStreamChunk",
    "ToolStatus",
    "UnsupportedCapabilityError",
    "UnknownHandleError",
    "build_tool_manager",
]


def build_tool_manager(
    *,
    event_bus: EventBus,
    supervisor: Supervisor | None = None,
    configuration_manager: ConfigurationManager | None = None,
) -> ToolManager:
    """Constructs a Tool Manager bound to the given event bus.

    If no Supervisor is given, Tool Manager creates its own (bound to the
    same bus) so every registered adapter still gets health monitoring and
    automatic restart -- see service.py.

    `configuration_manager` is optional and additive -- see
    `ToolManager.default_adapter_config()`'s docstring; omitting it
    reproduces every prior behavior of this class exactly.
    """
    return ToolManager(event_bus=event_bus, supervisor=supervisor, configuration_manager=configuration_manager)
