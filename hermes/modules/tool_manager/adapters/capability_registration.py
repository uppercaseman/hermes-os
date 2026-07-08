"""Shared `register_provider_capabilities(...)` helper.

Each adapter file has a tiny `register_with_capability_registry(...)`
helper that calls this. Keeps the per-adapter function identical so the
canonical capability matrix in `provider_config.py` is the single
source of truth -- adding a new canonical capability, or a new provider,
requires no per-adapter code.
"""
from __future__ import annotations

from typing import Any

from hermes.modules.capability_registry.models import CapabilityProviderRegistration
from hermes.modules.tool_manager.adapters.provider_config import supported_capabilities


def register_provider_capabilities(
    registry: Any,
    *,
    provider_name: str,
    tool_name: str,
    priority: int = 100,
    cost_per_call: float = 0.0,
    declared_latency_ms: float = 0.0,
) -> None:
    """Registers `tool_name` as a provider for every canonical
    capability `provider_name` supports. Reads the canonical capability
    list from `provider_config.supported_capabilities(provider_name)`;
    adding a new capability to the matrix requires zero changes here.
    """
    for capability in supported_capabilities(provider_name):
        registry.register_provider(
            CapabilityProviderRegistration(
                capability=capability,
                tool_name=tool_name,
                priority=priority,
                cost_per_call=cost_per_call,
                declared_latency_ms=declared_latency_ms,
            )
        )


__all__ = ["register_provider_capabilities"]
