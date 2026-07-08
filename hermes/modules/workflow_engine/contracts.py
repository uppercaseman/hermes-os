"""Narrow Protocols for Workflow Engine's optional collaborators.

Workflow Engine depends on the SHAPE of Tool Manager / Memory Manager /
Capability Registry it actually uses, not their concrete classes -- the
same "depend on a Protocol, not a concretion" pattern used throughout
this codebase (Commander's contracts.py, Tool Manager's ToolAdapter).
This keeps step execution testable with lightweight fakes, and means a
change to any of those modules only breaks this file if it touches
exactly these methods.
"""
from __future__ import annotations

from typing import Any, Protocol

from hermes.modules.capability_registry.models import CapabilitySelection
from hermes.modules.memory_manager.models import MemoryEntry
from hermes.modules.tool_manager.models import ToolInvocationRequest, ToolInvocationResult


class ToolInvoker(Protocol):
    """What Workflow Engine needs from Tool Manager: just `invoke`."""

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult: ...


class CapabilitySelector(Protocol):
    """What Workflow Engine needs from the Capability Registry: just
    `select`, used when a tool_call step names a capability instead of a
    specific tool."""

    async def select(self, capability: str) -> CapabilitySelection: ...


class MemoryStore(Protocol):
    """What Workflow Engine needs from Memory Manager: `save` for
    memory_write steps, `get_by_key` for memory_read steps."""

    async def save(
        self,
        *,
        requesting_agent_id: str,
        scope: Any,
        key: str,
        value: dict[str, Any],
        owner_agent_id: str | None = None,
        session_id: str | None = None,
        workflow_run_id: Any = None,
        tags: list[str] | None = None,
        backlinks: list[Any] | None = None,
        ttl_seconds: float | None = None,
    ) -> MemoryEntry: ...

    async def get_by_key(
        self,
        *,
        requesting_agent_id: str,
        scope: Any,
        key: str,
        owner_agent_id: str | None = None,
        session_id: str | None = None,
        workflow_run_id: Any = None,
    ) -> MemoryEntry | None: ...
