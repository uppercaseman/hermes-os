"""Public entry point for the Capability Registry.

Everything outside this module imports from here, never from service.py
directly -- mirrors every other module's interface.py convention.
"""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.modules.capability_registry.contracts import SelectionStrategy
from hermes.modules.capability_registry.errors import UnknownCapabilityError, UnknownProviderError
from hermes.modules.capability_registry.models import (
    CapabilityCandidate,
    CapabilityProviderRegistration,
    CapabilitySelection,
    ProviderHealth,
    ProviderHealthState,
)
from hermes.modules.capability_registry.service import CapabilityRegistry
from hermes.modules.capability_registry.strategies import PriorityCostLatencyStrategy

__all__ = [
    "CapabilityRegistry",
    "CapabilityProviderRegistration",
    "CapabilitySelection",
    "CapabilityCandidate",
    "ProviderHealth",
    "ProviderHealthState",
    "SelectionStrategy",
    "PriorityCostLatencyStrategy",
    "UnknownCapabilityError",
    "UnknownProviderError",
    "build_capability_registry",
]


def build_capability_registry(
    *, event_bus: EventBus | None = None, strategy: SelectionStrategy | None = None
) -> CapabilityRegistry:
    """Constructs a Capability Registry.

    If `event_bus` is given, call `start()` on the result to begin
    automatic provider-health tracking from Supervisor lifecycle events;
    without it (or without calling `start()`), health is tracked purely
    via explicit `update_health()` calls.
    """
    return CapabilityRegistry(event_bus=event_bus, strategy=strategy)
