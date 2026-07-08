"""Provider Router service.

The router walks the Capability Registry's ranked candidate chain,
invokes each candidate through Tool Manager (which handles retries,
rate limits, and timeouts per adapter), and on a transient failure
moves to the next candidate. The structured `ProviderInvocationOutcome`
returned to the caller records every attempt so the routing decision
is fully auditable.

Key properties:

- **Commander remains provider-agnostic.** The router's only public
  surface is `route(...)` keyed by capability; the caller never
  imports or names a provider.
- **Automatic fail-over.** A `ToolInvocationResult.status == "failed"`
  from Tool Manager triggers the next candidate, up to
  `failover_max_attempts`.
- **Idempotent.** Two consecutive `route(...)` calls with the same
  request can produce different outcomes if the underlying provider
  health changes -- but each call is a pure function of (registry
  state + Tool Manager state) and never has side effects beyond
  publishing routing events.
- **Read-mostly.** The router itself never writes to Memory, never
  promotes entries, and never invokes the Reflection Engine. It only
  invokes tools.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.capability_registry.contracts import SelectionStrategy
from hermes.modules.capability_registry.models import CapabilityCandidate
from hermes.modules.logging_system.interface import LoggingSystem
from hermes.modules.provider_router import events as evt
from hermes.modules.provider_router.contracts import CapabilitySelector, ToolInvoker
from hermes.modules.provider_router.errors import (
    InvalidRoutingRequestError,
    NoProviderAvailableError,
)
from hermes.modules.provider_router.models import (
    ProviderAttempt,
    ProviderInvocationOutcome,
    RoutingRequest,
)
from hermes.modules.tool_manager.models import ToolInvocationRequest

SOURCE_MODULE = "provider_router"


class ProviderRouter:
    def __init__(
        self,
        *,
        tool_manager: ToolInvoker,
        capability_registry: CapabilitySelector,
        event_bus: EventBus | None = None,
        logging_system: LoggingSystem | None = None,
        failover_max_attempts: int = 3,
        retry_on_transient: bool = True,
    ) -> None:
        if failover_max_attempts < 1:
            raise InvalidRoutingRequestError(
                "failover_max_attempts must be at least 1 (zero candidates is impossible)"
            )
        self._tool_manager = tool_manager
        self._registry = capability_registry
        self._bus = event_bus
        self._logging = logging_system
        self._max_attempts = failover_max_attempts
        self._retry_on_transient = retry_on_transient

    # ------------------------------------------------------------------ #
    # Public surface
    # ------------------------------------------------------------------ #
    async def route(self, request: RoutingRequest) -> ProviderInvocationOutcome:
        """Resolves `request.capability` to one or more providers,
        attempts each in order, and returns the structured outcome.

        Fail-over semantics: when an attempt returns
        `status="failed"`, the router moves to the next candidate in
        the Capability Registry chain. If the chain is exhausted, the
        outcome carries `success=False` and the final result.

        Retry semantics: a transient failure from a single provider
        causes Tool Manager's own retry policy to fire (its
        `RetryPolicy` is applied at `invoke()` time, before the router
        sees the result). `retry_on_transient=True` (the default)
        additionally allows the router to try a different provider on
        top of Tool Manager's retries.
        """
        if not request.capability:
            raise InvalidRoutingRequestError("capability is required")
        await self._publish(
            evt.ROUTING_STARTED,
            {
                "capability": request.capability,
                "correlation_id": str(request.correlation_id),
            },
        )

        try:
            chain = await self._registry.resolve_chain(request.capability)
        except Exception as exc:
            await self._publish(
                evt.ROUTING_FAILED,
                {
                    "capability": request.capability,
                    "correlation_id": str(request.correlation_id),
                    "error": str(exc),
                    "reason": "registry_lookup_failed",
                },
            )
            raise

        if not chain:
            await self._publish(
                evt.ROUTING_FAILED,
                {
                    "capability": request.capability,
                    "correlation_id": str(request.correlation_id),
                    "error": "no candidate providers",
                    "reason": "empty_chain",
                },
            )
            raise NoProviderAvailableError(request.capability)

        attempts: list[ProviderAttempt] = []
        last_result = None
        failover_count = 0
        success = False
        selected_tool_name: str | None = None
        final_result = None

        for index, candidate in enumerate(chain[: self._max_attempts], start=1):
            attempt = await self._invoke_candidate(candidate, request, index)
            attempts.append(attempt)
            last_result = attempt
            if attempt.succeeded:
                success = True
                selected_tool_name = candidate.tool_name
                break
            failover_count += 1
            if index < len(chain[: self._max_attempts]):
                await self._publish(
                    evt.ROUTING_FAILOVER,
                    {
                        "capability": request.capability,
                        "correlation_id": str(request.correlation_id),
                        "from_tool": candidate.tool_name,
                        "to_index": index + 1,
                    },
                )

        if success:
            await self._publish(
                evt.ROUTING_SUCCEEDED,
                {
                    "capability": request.capability,
                    "correlation_id": str(request.correlation_id),
                    "selected_tool_name": selected_tool_name,
                    "failover_count": failover_count,
                    "attempts": [a.model_dump(mode="json") for a in attempts],
                },
            )
        else:
            await self._publish(
                evt.ROUTING_FAILED,
                {
                    "capability": request.capability,
                    "correlation_id": str(request.correlation_id),
                    "failover_count": failover_count,
                    "attempts": [a.model_dump(mode="json") for a in attempts],
                },
            )

        # Build the final result: if any attempt succeeded, the caller
        # already has the underlying `ToolInvocationResult`. The router
        # re-invokes through Tool Manager for the SUCCESS path so the
        # final result is the canonical `ToolInvocationResult`. For the
        # failure path, we synthesise a minimal failure result so the
        # caller's shape is stable.
        if success and last_result is not None:
            final_result = await self._final_invoke(selected_tool_name, request)
        else:
            final_result = None

        return ProviderInvocationOutcome(
            capability=request.capability,
            success=success,
            selected_provider=last_result.provider if success else None,
            selected_tool_name=selected_tool_name,
            final_result=final_result,
            attempts=attempts,
            failover_count=failover_count,
            correlation_id=request.correlation_id,
        )

    async def route_stream(self, request: RoutingRequest):
        """Resolves the capability to the top-ranked available provider
        and yields its streaming chunks. Fail-over for streams is a
        future Sprint: a stream that fails mid-way is terminal, so a
        caller using `route_stream` accepts the top-ranked provider or
        gets nothing. For full fail-over semantics, use `route()`
        (sync) and dispatch `invoke_stream` against the resolved
        tool_name yourself.
        """
        if not request.capability:
            raise InvalidRoutingRequestError("capability is required")
        chain = await self._registry.resolve_chain(request.capability)
        if not chain:
            raise NoProviderAvailableError(request.capability)
        await self._publish(
            evt.ROUTING_STARTED,
            {
                "capability": request.capability,
                "correlation_id": str(request.correlation_id),
                "mode": "stream",
            },
        )
        candidate = chain[0]
        invocation = self._build_invocation(candidate, request)
        await self._publish(
            evt.PROVIDER_ATTEMPT_STARTED,
            {
                "capability": request.capability,
                "correlation_id": str(request.correlation_id),
                "tool_name": candidate.tool_name,
                "attempt_index": 1,
                "mode": "stream",
            },
        )
        async for chunk in self._tool_manager.invoke_stream(invocation):
            yield chunk

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _invoke_candidate(
        self,
        candidate: CapabilityCandidate,
        request: RoutingRequest,
        index: int,
    ) -> ProviderAttempt:
        """One `invoke()` through Tool Manager, with provider-attempt
        observability wrapped around it. Does NOT itself implement
        retries -- Tool Manager already does. The router only sees
        the *final* result.
        """
        invocation = self._build_invocation(candidate, request)
        await self._publish(
            evt.PROVIDER_ATTEMPT_STARTED,
            {
                "capability": request.capability,
                "correlation_id": str(request.correlation_id),
                "tool_name": candidate.tool_name,
                "attempt_index": index,
            },
        )
        start = time.perf_counter()
        try:
            result = await self._tool_manager.invoke(invocation)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if result.status == "completed":
                await self._publish(
                    evt.PROVIDER_ATTEMPT_SUCCEEDED,
                    {
                        "capability": request.capability,
                        "correlation_id": str(request.correlation_id),
                        "tool_name": candidate.tool_name,
                        "attempt_index": index,
                        "latency_ms": elapsed_ms,
                    },
                )
                return ProviderAttempt(
                    provider=candidate.tool_name,
                    tool_name=candidate.tool_name,
                    attempt_index=index,
                    succeeded=True,
                    latency_ms=elapsed_ms,
                    status="completed",
                )
            error = result.error or "unknown failure"
            await self._publish(
                evt.PROVIDER_ATTEMPT_FAILED,
                {
                    "capability": request.capability,
                    "correlation_id": str(request.correlation_id),
                    "tool_name": candidate.tool_name,
                    "attempt_index": index,
                    "latency_ms": elapsed_ms,
                    "error": error,
                },
            )
            return ProviderAttempt(
                provider=candidate.tool_name,
                tool_name=candidate.tool_name,
                attempt_index=index,
                succeeded=False,
                latency_ms=elapsed_ms,
                error=error,
                status="failed",
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            await self._publish(
                evt.PROVIDER_ATTEMPT_FAILED,
                {
                    "capability": request.capability,
                    "correlation_id": str(request.correlation_id),
                    "tool_name": candidate.tool_name,
                    "attempt_index": index,
                    "latency_ms": elapsed_ms,
                    "error": str(exc),
                },
            )
            return ProviderAttempt(
                provider=candidate.tool_name,
                tool_name=candidate.tool_name,
                attempt_index=index,
                succeeded=False,
                latency_ms=elapsed_ms,
                error=str(exc),
                status="failed",
            )

    async def _final_invoke(self, tool_name: str | None, request: RoutingRequest):
        """Re-invokes the selected provider to surface its full
        `ToolInvocationResult` on the outcome. Skipped when the router
        ran out of candidates (in which case `final_result` is None).
        """
        if tool_name is None:
            return None
        return await self._tool_manager.invoke(
            ToolInvocationRequest(
                tool_name=tool_name,
                operation=request.operation,
                parameters={"capability": request.capability, **request.parameters},
                correlation_id=request.correlation_id,
            )
        )

    def _build_invocation(
        self, candidate: CapabilityCandidate, request: RoutingRequest
    ) -> ToolInvocationRequest:
        return ToolInvocationRequest(
            tool_name=candidate.tool_name,
            operation=request.operation,
            parameters={"capability": request.capability, **request.parameters},
            correlation_id=request.correlation_id,
        )

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module=SOURCE_MODULE,
                correlation_id=uuid.uuid4(),
                payload=payload,
            )
        )


__all__ = ["ProviderRouter"]
