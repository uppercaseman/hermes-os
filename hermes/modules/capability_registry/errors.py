"""Capability Registry-specific exception types.

Both are caller/config misuse (asking for a capability that was never
registered, or pinning an override to a provider that was never declared
capable of it) -- consistent with the rest of Hermes, these raise, while
runtime conditions (every registered provider currently unavailable)
return a structured `CapabilitySelection` instead.
"""
from __future__ import annotations


class UnknownCapabilityError(Exception):
    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__(f"no provider has ever been registered for capability {capability!r}")


class UnknownProviderError(Exception):
    def __init__(self, capability: str, tool_name: str) -> None:
        self.capability = capability
        self.tool_name = tool_name
        super().__init__(f"tool {tool_name!r} is not a registered provider for capability {capability!r}")
