"""Pydantic data contracts for the Provider Router.

These types flow between Commander (the router's primary caller),
Capability Registry (the candidate source), Tool Manager (the
invocation substrate), and the event bus (the observability sink).
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from hermes.modules.tool_manager.models import ToolInvocationRequest, ToolInvocationResult, ToolStreamChunk


class RoutingRequest(BaseModel):
    """A single routing request: one capability + the parameters the
    provider needs. The router does not interpret `parameters`; the
    chosen adapter does."""

    capability: str
    parameters: dict = Field(default_factory=dict)
    operation: str = "invoke"
    correlation_id: uuid.UUID = Field(default_factory=uuid.uuid4)


class ProviderAttempt(BaseModel):
    """One entry in the routing trail: which provider was tried, the
    outcome, and how long it took."""

    provider: str
    tool_name: str
    attempt_index: int
    succeeded: bool
    latency_ms: float
    error: str | None = None
    status: Literal["completed", "failed"] = "failed"


class ProviderInvocationOutcome(BaseModel):
    """What the router returns for a `route(...)` call: the final result
    plus the full attempt trail so a caller can audit which providers
    were tried and why the last one was selected."""

    capability: str
    success: bool
    selected_provider: str | None
    selected_tool_name: str | None
    final_result: ToolInvocationResult | None
    attempts: list[ProviderAttempt] = Field(default_factory=list)
    failover_count: int = 0
    correlation_id: uuid.UUID


__all__ = [
    "RoutingRequest",
    "ProviderAttempt",
    "ProviderInvocationOutcome",
    "ToolInvocationRequest",
    "ToolInvocationResult",
    "ToolStreamChunk",
]
