"""Provider Router Protocol contracts.

Defines narrow surfaces for the router's two collaborators:

- `ToolInvoker` is what the router actually calls -- it has the same
  shape as `ToolManager.invoke()` and `invoke_stream()`. Tests can
  pass a stub.

- `CapabilitySelector` is what the router asks for a candidate chain.
  Same shape as `CapabilityRegistry.resolve_chain()`.

The router never imports `ToolManager` or `CapabilityRegistry`
directly outside its `__init__`, so the dependency is decoupled.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from hermes.modules.capability_registry.models import CapabilityCandidate
from hermes.modules.tool_manager.models import (
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolStreamChunk,
)


@runtime_checkable
class ToolInvoker(Protocol):
    """Subset of Tool Manager the router depends on."""

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        ...

    def invoke_stream(self, request: ToolInvocationRequest):  # AsyncIterator[ToolStreamChunk]
        ...


@runtime_checkable
class CapabilitySelector(Protocol):
    async def resolve_chain(self, capability: str) -> list[CapabilityCandidate]:
        ...


__all__ = ["ToolInvoker", "CapabilitySelector"]
