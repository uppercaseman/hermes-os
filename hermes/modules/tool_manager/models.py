"""Pydantic data contracts for the Tool Manager.

These are the types that flow between Tool Manager, its adapters, and
whatever calls it (Commander today via the `ToolResolver` planning
contract; a future Task Queue worker for actual execution).
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from hermes.core.supervisor.policy import RetryPolicy


class ToolCapabilities(BaseModel):
    """What one adapter instance supports. Tool Manager checks these
    before calling a capability-gated method (e.g. `invoke_stream`) so an
    adapter that can't do something fails predictably rather than with
    whatever provider-specific error it happens to raise."""

    supports_sync: bool = True
    supports_streaming: bool = False
    requires_auth: bool = True


class AuthConfig(BaseModel):
    """How an adapter authenticates. `credential_ref` is a REFERENCE to
    where the real secret lives (an env var name, a secret-store key) --
    never the secret itself. Resolving that reference into an actual
    credential is the future Configuration Manager's job, consistent with
    the architecture doc's "secrets never in plain config" rule; Tool
    Manager only carries the reference through to the adapter.
    """

    auth_type: Literal["api_key", "oauth", "none"] = "api_key"
    credential_ref: str | None = None


class RateLimitPolicy(BaseModel):
    """Token-bucket parameters: at most `max_calls` per `per_seconds`,
    refilling continuously. See rate_limiter.py."""

    max_calls: int = Field(default=60, ge=1)
    per_seconds: float = Field(default=60.0, gt=0)


class ToolAdapterConfig(BaseModel):
    """Registration-time configuration for one adapter instance -- what
    Tool Manager needs to know to supervise, rate-limit, and retry it.
    Provider-specific configuration (e.g. a model name) belongs to the
    adapter itself, not here."""

    name: str
    auth: AuthConfig = Field(default_factory=AuthConfig)
    rate_limit: RateLimitPolicy = Field(default_factory=RateLimitPolicy)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    invocation_timeout_seconds: float = Field(default=30.0, gt=0)
    health_check_interval_seconds: float = Field(default=30.0, gt=0)


class ToolInvocationRequest(BaseModel):
    """A single call into one adapter. `operation` and `parameters` are
    opaque to Tool Manager -- it never interprets them, only routes and
    wraps the call with retry/rate-limit/timeout."""

    tool_name: str
    operation: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    correlation_id: uuid.UUID = Field(default_factory=uuid.uuid4)


class ToolInvocationResult(BaseModel):
    tool_name: str
    correlation_id: uuid.UUID
    status: Literal["completed", "failed"]
    output: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 1


class ToolInvocationHandle(BaseModel):
    """Returned by `invoke_async` -- a receipt for retrieving the result
    later via `get_result`/`await_result`."""

    handle_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tool_name: str
    correlation_id: uuid.UUID


class ToolStreamChunk(BaseModel):
    """One item from `invoke_stream`. A failure ends the stream with a
    final chunk carrying `error` set, rather than raising -- consistent
    with the rest of Hermes never raising a collaborator's failure across
    an interface boundary."""

    tool_name: str
    correlation_id: uuid.UUID
    sequence: int
    data: dict[str, Any] = Field(default_factory=dict)
    is_final: bool = False
    error: str | None = None


class ToolStatus(BaseModel):
    """Observable status of one registered adapter: lifecycle state comes
    from the Supervisor managing it; the counters are Tool Manager's own."""

    name: str
    provider: str
    state: Literal["starting", "running", "restarting", "stopped", "failed"]
    capabilities: ToolCapabilities
    total_invocations: int = 0
    total_failures: int = 0
