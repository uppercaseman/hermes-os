"""Public entry point for the Provider Router.

Mirrors every other module's interface.py convention: outside callers
import from here, never from service.py directly.
"""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.modules.capability_registry.interface import CapabilityRegistry
from hermes.modules.logging_system.interface import LoggingSystem
from hermes.modules.provider_router.service import ProviderRouter
from hermes.modules.tool_manager.interface import ToolManager

__all__ = ["ProviderRouter", "build_provider_router"]


def build_provider_router(
    *,
    tool_manager: ToolManager,
    capability_registry: CapabilityRegistry,
    event_bus: EventBus | None = None,
    logging_system: LoggingSystem | None = None,
    failover_max_attempts: int = 3,
    retry_on_transient: bool = True,
) -> ProviderRouter:
    """Constructs a Provider Router. The router is a thin coordinator
    over Tool Manager + Capability Registry; both are required.

    `failover_max_attempts` bounds how many candidate providers the
    router will try before giving up. `retry_on_transient` toggles
    whether a single provider gets retried on transient failure before
    the router moves to the next candidate (Tool Manager's own retry
    policy still applies on top).
    """
    return ProviderRouter(
        tool_manager=tool_manager,
        capability_registry=capability_registry,
        event_bus=event_bus,
        logging_system=logging_system,
        failover_max_attempts=failover_max_attempts,
        retry_on_transient=retry_on_transient,
    )
