"""Test doubles satisfying Workflow Engine's narrow collaborator
Protocols -- not real Tool Manager / Memory Manager / Capability
Registry implementations, used only to exercise the engine's own
scheduling/branching/retry logic in isolation.
"""
from __future__ import annotations

import asyncio
from typing import Any

from hermes.modules.capability_registry.models import CapabilitySelection
from hermes.modules.memory_manager.models import MemoryEntry
from hermes.modules.tool_manager.models import ToolInvocationRequest, ToolInvocationResult


class FakeToolInvoker:
    def __init__(self, *, outcomes: list[str] | None = None, output: dict[str, Any] | None = None) -> None:
        self._outcomes = list(outcomes) if outcomes is not None else ["ok"]
        self._output = output or {}
        self.calls: list[ToolInvocationRequest] = []

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        self.calls.append(request)
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if outcome == "raise":
            raise RuntimeError("scripted tool invocation failure")
        if outcome == "fail":
            return ToolInvocationResult(
                tool_name=request.tool_name, correlation_id=request.correlation_id, status="failed", error="scripted failure"
            )
        return ToolInvocationResult(
            tool_name=request.tool_name,
            correlation_id=request.correlation_id,
            status="completed",
            output={**self._output, "echo_parameters": request.parameters},
        )


class HangingToolInvoker:
    """Simulates a tool call that never returns in time -- for testing
    step-level timeouts."""

    def __init__(self, *, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        await asyncio.sleep(self._delay_seconds)
        return ToolInvocationResult(tool_name=request.tool_name, correlation_id=request.correlation_id, status="completed")


class FakeCapabilitySelector:
    def __init__(self, *, selected: str | None) -> None:
        self._selected = selected

    async def select(self, capability: str) -> CapabilitySelection:
        return CapabilitySelection(
            capability=capability,
            selected=self._selected,
            reason=None if self._selected else "no provider available",
        )


class FakeMemoryStore:
    def __init__(self) -> None:
        self._entries: dict[tuple, MemoryEntry] = {}

    async def save(
        self,
        *,
        requesting_agent_id: str,
        scope,
        key: str,
        value: dict[str, Any],
        owner_agent_id=None,
        session_id=None,
        workflow_run_id=None,
        tags=None,
        backlinks=None,
        ttl_seconds=None,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            scope=scope, owner_agent_id=owner_agent_id, session_id=session_id, workflow_run_id=workflow_run_id,
            key=key, value=value, tags=tags or [], backlinks=backlinks or [],
        )
        self._entries[(scope, owner_agent_id, session_id, workflow_run_id, key)] = entry
        return entry

    async def get_by_key(
        self, *, requesting_agent_id: str, scope, key: str, owner_agent_id=None, session_id=None, workflow_run_id=None
    ) -> MemoryEntry | None:
        return self._entries.get((scope, owner_agent_id, session_id, workflow_run_id, key))
