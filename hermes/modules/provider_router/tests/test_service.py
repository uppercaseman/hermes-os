"""Provider Router unit tests.

Strategy: stub the two collaborators (`ToolInvoker`,
`CapabilitySelector`) with in-memory fakes. The router itself is the
system under test -- the goal is to verify the routing logic, not the
collaborators.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator

import pytest

from hermes.core.event_bus.models import Event
from hermes.modules.capability_registry.models import CapabilityCandidate
from hermes.modules.provider_router import build_provider_router
from hermes.modules.provider_router.errors import (
    InvalidRoutingRequestError,
    NoProviderAvailableError,
)
from hermes.modules.provider_router.models import RoutingRequest
from hermes.modules.tool_manager.models import (
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolStreamChunk,
)


# ---------------------------------------------------------------------- #
# Test doubles
# ---------------------------------------------------------------------- #
class FakeToolInvoker:
    """Drop-in `ToolInvoker` that records every call and dispatches to
    a configurable per-tool response. Supports raising for transient
    errors that bypass Tool Manager's contract."""

    def __init__(self) -> None:
        self.calls: list[ToolInvocationRequest] = []
        self.completed: dict[str, ToolInvocationResult] = {}
        self.failed: dict[str, str] = {}
        self.exceptions: dict[str, Exception] = {}
        self.stream_chunks: dict[str, list[ToolStreamChunk]] = {}

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        self.calls.append(request)
        if request.tool_name in self.exceptions:
            raise self.exceptions[request.tool_name]
        if request.tool_name in self.completed:
            result = self.completed[request.tool_name]
            return ToolInvocationResult(
                tool_name=request.tool_name,
                correlation_id=request.correlation_id,
                status=result.status,
                output=result.output,
                error=result.error,
            )
        if request.tool_name in self.failed:
            return ToolInvocationResult(
                tool_name=request.tool_name,
                correlation_id=request.correlation_id,
                status="failed",
                error=self.failed[request.tool_name],
            )
        return ToolInvocationResult(
            tool_name=request.tool_name,
            correlation_id=request.correlation_id,
            status="completed",
            output={"echo": True},
        )

    async def invoke_stream(
        self, request: ToolInvocationRequest
    ) -> AsyncIterator[ToolStreamChunk]:
        # Honor the async-generator contract: yield must be reachable
        # even on the error path.
        self.calls.append(request)
        chunks = self.stream_chunks.get(request.tool_name, [])
        for chunk in chunks:
            yield chunk
        return
        yield  # pragma: no cover -- satisfies `async for` typing on no-chunk path


class FakeCapabilitySelector:
    """Resolves any capability to a configurable candidate chain."""

    def __init__(self) -> None:
        self.chains: dict[str, list[CapabilityCandidate]] = {}
        self.raise_on: set[str] = set()

    async def resolve_chain(self, capability: str) -> list[CapabilityCandidate]:
        if capability in self.raise_on:
            raise RuntimeError(f"registry boom for {capability}")
        return list(self.chains.get(capability, []))


class FakeEventBus:
    """Records published events; never raises."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.events.append(event)


def _candidate(tool_name: str, priority: int = 1) -> CapabilityCandidate:
    return CapabilityCandidate(
        tool_name=tool_name,
        priority=priority,
        cost_per_call=0.0,
        latency_ms=10.0,
        health_state="healthy",
    )


def _completed(tool_name: str, correlation_id: uuid.UUID | None = None) -> ToolInvocationResult:
    return ToolInvocationResult(
        tool_name=tool_name,
        correlation_id=correlation_id or uuid.uuid4(),
        status="completed",
        output={"value": f"hello from {tool_name}"},
    )


# ---------------------------------------------------------------------- #
# Construction
# ---------------------------------------------------------------------- #
def test_build_provider_router_returns_instance():
    tm = FakeToolInvoker()
    cr = FakeCapabilitySelector()
    router = build_provider_router(tool_manager=tm, capability_registry=cr)
    assert router is not None


def test_constructor_rejects_zero_failover_attempts():
    tm = FakeToolInvoker()
    cr = FakeCapabilitySelector()
    with pytest.raises(InvalidRoutingRequestError):
        build_provider_router(
            tool_manager=tm, capability_registry=cr, failover_max_attempts=0
        )


# ---------------------------------------------------------------------- #
# Empty chain
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_empty_chain_raises_no_provider_available():
    tm = FakeToolInvoker()
    cr = FakeCapabilitySelector()
    bus = FakeEventBus()
    router = build_provider_router(tool_manager=tm, capability_registry=cr, event_bus=bus)
    with pytest.raises(NoProviderAvailableError):
        await router.route(RoutingRequest(capability="reasoning"))
    # The empty-chain ROUTING_FAILED event is published before raise
    assert any(
        e.event_type == "provider_router.routing.failed" for e in bus.events
    )


# ---------------------------------------------------------------------- #
# Single candidate
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_single_successful_candidate_returns_completed():
    tm = FakeToolInvoker()
    tm.completed["openai"] = _completed("openai")
    cr = FakeCapabilitySelector()
    cr.chains["reasoning"] = [_candidate("openai")]
    bus = FakeEventBus()
    router = build_provider_router(tool_manager=tm, capability_registry=cr, event_bus=bus)

    outcome = await router.route(RoutingRequest(capability="reasoning"))

    assert outcome.success is True
    assert outcome.selected_tool_name == "openai"
    assert outcome.failover_count == 0
    assert len(outcome.attempts) == 1
    assert outcome.attempts[0].succeeded is True
    assert outcome.final_result is not None
    assert outcome.final_result.status == "completed"


# ---------------------------------------------------------------------- #
# Multi-candidate fail-over
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_failover_to_second_candidate_after_first_fails():
    tm = FakeToolInvoker()
    tm.failed["openai"] = "rate limited"
    tm.completed["anthropic"] = _completed("anthropic")
    cr = FakeCapabilitySelector()
    cr.chains["reasoning"] = [_candidate("openai", priority=1), _candidate("anthropic", priority=2)]
    bus = FakeEventBus()
    router = build_provider_router(tool_manager=tm, capability_registry=cr, event_bus=bus)

    outcome = await router.route(RoutingRequest(capability="reasoning"))

    assert outcome.success is True
    assert outcome.selected_tool_name == "anthropic"
    assert outcome.failover_count == 1
    assert [a.tool_name for a in outcome.attempts] == ["openai", "anthropic"]
    assert outcome.attempts[0].succeeded is False
    assert outcome.attempts[1].succeeded is True
    # ROUTING_FAILOVER was published exactly once
    failover_events = [
        e for e in bus.events if e.event_type == "provider_router.routing.failover"
    ]
    assert len(failover_events) == 1


@pytest.mark.asyncio
async def test_failover_continues_when_invocation_raises():
    tm = FakeToolInvoker()
    tm.exceptions["openai"] = RuntimeError("connection reset")
    tm.completed["anthropic"] = _completed("anthropic")
    cr = FakeCapabilitySelector()
    cr.chains["reasoning"] = [_candidate("openai"), _candidate("anthropic")]
    router = build_provider_router(tool_manager=tm, capability_registry=cr)

    outcome = await router.route(RoutingRequest(capability="reasoning"))

    assert outcome.success is True
    assert outcome.selected_tool_name == "anthropic"
    assert outcome.attempts[0].error == "connection reset"


# ---------------------------------------------------------------------- #
# Exhaustion
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_exhaustion_returns_unsuccessful_outcome():
    tm = FakeToolInvoker()
    tm.failed["openai"] = "5xx"
    tm.failed["anthropic"] = "timeout"
    tm.failed["gemini"] = "rate limit"
    cr = FakeCapabilitySelector()
    cr.chains["reasoning"] = [
        _candidate("openai"),
        _candidate("anthropic"),
        _candidate("gemini"),
    ]
    bus = FakeEventBus()
    router = build_provider_router(
        tool_manager=tm, capability_registry=cr, event_bus=bus, failover_max_attempts=3
    )

    outcome = await router.route(RoutingRequest(capability="reasoning"))

    assert outcome.success is False
    assert outcome.selected_tool_name is None
    assert outcome.failover_count == 3
    assert len(outcome.attempts) == 3
    assert all(a.succeeded is False for a in outcome.attempts)
    assert outcome.final_result is None
    assert any(
        e.event_type == "provider_router.routing.failed" for e in bus.events
    )


@pytest.mark.asyncio
async def test_failover_max_attempts_bounds_chain_iteration():
    """A chain longer than `failover_max_attempts` is truncated."""
    tm = FakeToolInvoker()
    for name in ("a", "b", "c", "d", "e"):
        tm.failed[name] = "down"
    cr = FakeCapabilitySelector()
    cr.chains["reasoning"] = [_candidate(n) for n in ("a", "b", "c", "d", "e")]
    router = build_provider_router(
        tool_manager=tm, capability_registry=cr, failover_max_attempts=2
    )

    outcome = await router.route(RoutingRequest(capability="reasoning"))

    assert outcome.success is False
    assert len(outcome.attempts) == 2
    assert [a.tool_name for a in outcome.attempts] == ["a", "b"]


# ---------------------------------------------------------------------- #
# Ordering & termination
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_successful_attempt_terminates_chain():
    """A success on attempt 2 should not invoke attempts 3+."""
    tm = FakeToolInvoker()
    tm.failed["openai"] = "5xx"
    tm.completed["anthropic"] = _completed("anthropic")
    tm.completed["gemini"] = _completed("gemini")
    cr = FakeCapabilitySelector()
    cr.chains["reasoning"] = [
        _candidate("openai"),
        _candidate("anthropic"),
        _candidate("gemini"),
    ]
    router = build_provider_router(tool_manager=tm, capability_registry=cr)

    outcome = await router.route(RoutingRequest(capability="reasoning"))

    assert outcome.success is True
    assert outcome.selected_tool_name == "anthropic"
    # Only two invoke calls were made
    invoked_tools = [c.tool_name for c in tm.calls]
    assert "gemini" not in invoked_tools


# ---------------------------------------------------------------------- #
# Validation
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_missing_capability_raises_invalid_request():
    tm = FakeToolInvoker()
    cr = FakeCapabilitySelector()
    router = build_provider_router(tool_manager=tm, capability_registry=cr)
    with pytest.raises(InvalidRoutingRequestError):
        await router.route(RoutingRequest(capability=""))


# ---------------------------------------------------------------------- #
# Registry lookup failure
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_registry_lookup_failure_propagates_and_publishes_event():
    tm = FakeToolInvoker()
    cr = FakeCapabilitySelector()
    cr.raise_on.add("reasoning")
    bus = FakeEventBus()
    router = build_provider_router(tool_manager=tm, capability_registry=cr, event_bus=bus)

    with pytest.raises(RuntimeError):
        await router.route(RoutingRequest(capability="reasoning"))

    failed = [
        e for e in bus.events if e.event_type == "provider_router.routing.failed"
    ]
    assert len(failed) == 1
    assert failed[0].payload["reason"] == "registry_lookup_failed"


# ---------------------------------------------------------------------- #
# Event publication sequence
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_event_sequence_for_successful_route():
    tm = FakeToolInvoker()
    tm.completed["openai"] = _completed("openai")
    cr = FakeCapabilitySelector()
    cr.chains["reasoning"] = [_candidate("openai")]
    bus = FakeEventBus()
    router = build_provider_router(tool_manager=tm, capability_registry=cr, event_bus=bus)

    await router.route(RoutingRequest(capability="reasoning"))

    types = [e.event_type for e in bus.events]
    # started, attempt_started, attempt_succeeded, routing_succeeded
    assert types == [
        "provider_router.routing.started",
        "provider_router.provider_attempt.started",
        "provider_router.provider_attempt.succeeded",
        "provider_router.routing.succeeded",
    ]


@pytest.mark.asyncio
async def test_event_sequence_for_exhausted_chain_includes_failover_events():
    tm = FakeToolInvoker()
    tm.failed["openai"] = "5xx"
    tm.failed["anthropic"] = "5xx"
    cr = FakeCapabilitySelector()
    cr.chains["reasoning"] = [_candidate("openai"), _candidate("anthropic")]
    bus = FakeEventBus()
    router = build_provider_router(tool_manager=tm, capability_registry=cr, event_bus=bus)

    await router.route(RoutingRequest(capability="reasoning"))

    types = [e.event_type for e in bus.events]
    assert types == [
        "provider_router.routing.started",
        "provider_router.provider_attempt.started",
        "provider_router.provider_attempt.failed",
        "provider_router.routing.failover",
        "provider_router.provider_attempt.started",
        "provider_router.provider_attempt.failed",
        "provider_router.routing.failed",
    ]


# ---------------------------------------------------------------------- #
# Streaming
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_route_stream_yields_chunks_from_top_ranked_provider():
    tm = FakeToolInvoker()
    cid = uuid.uuid4()
    tm.stream_chunks["openai"] = [
        ToolStreamChunk(tool_name="openai", correlation_id=cid, sequence=0, data={"text": "hello"}),
        ToolStreamChunk(tool_name="openai", correlation_id=cid, sequence=1, data={"text": "world"}),
    ]
    cr = FakeCapabilitySelector()
    cr.chains["reasoning"] = [_candidate("openai"), _candidate("anthropic")]
    bus = FakeEventBus()
    router = build_provider_router(tool_manager=tm, capability_registry=cr, event_bus=bus)

    chunks: list[ToolStreamChunk] = []
    async for chunk in router.route_stream(RoutingRequest(capability="reasoning")):
        chunks.append(chunk)

    assert len(chunks) == 2
    assert [c.data["text"] for c in chunks] == ["hello", "world"]
    # Only the top-ranked provider was invoked
    assert len(tm.calls) == 1
    assert tm.calls[0].tool_name == "openai"


@pytest.mark.asyncio
async def test_route_stream_raises_on_empty_chain():
    tm = FakeToolInvoker()
    cr = FakeCapabilitySelector()
    router = build_provider_router(tool_manager=tm, capability_registry=cr)
    with pytest.raises(NoProviderAvailableError):
        async for _ in router.route_stream(RoutingRequest(capability="nope")):
            pass


# ---------------------------------------------------------------------- #
# Correlation ID propagation
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_correlation_id_is_propagated_into_invocations():
    tm = FakeToolInvoker()
    tm.completed["openai"] = _completed("openai")
    cr = FakeCapabilitySelector()
    cr.chains["reasoning"] = [_candidate("openai")]
    router = build_provider_router(tool_manager=tm, capability_registry=cr)

    cid = uuid.uuid4()
    await router.route(RoutingRequest(capability="reasoning", correlation_id=cid))

    # The router's final_invoke re-issues the call to get the canonical
    # `ToolInvocationResult`. Both invocations carry the correlation_id.
    assert all(c.correlation_id == cid for c in tm.calls)


# ---------------------------------------------------------------------- #
# No event bus
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_router_works_without_event_bus():
    tm = FakeToolInvoker()
    tm.completed["openai"] = _completed("openai")
    cr = FakeCapabilitySelector()
    cr.chains["reasoning"] = [_candidate("openai")]
    router = build_provider_router(tool_manager=tm, capability_registry=cr)

    outcome = await router.route(RoutingRequest(capability="reasoning"))
    assert outcome.success is True


# ---------------------------------------------------------------------- #
# Final result shape
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_final_result_is_a_canonical_tool_invocation_result():
    tm = FakeToolInvoker()
    tm.completed["openai"] = ToolInvocationResult(
        tool_name="openai",
        correlation_id=uuid.uuid4(),
        status="completed",
        output={"content": "abc", "tokens": 12},
    )
    cr = FakeCapabilitySelector()
    cr.chains["reasoning"] = [_candidate("openai")]
    router = build_provider_router(tool_manager=tm, capability_registry=cr)

    outcome = await router.route(RoutingRequest(capability="reasoning"))

    assert outcome.final_result is not None
    assert outcome.final_result.tool_name == "openai"
    assert outcome.final_result.output == {"content": "abc", "tokens": 12}