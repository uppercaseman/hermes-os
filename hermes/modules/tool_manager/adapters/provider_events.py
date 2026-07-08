"""Provider observability event vocabulary.

A provider adapter that wants to publish per-invocation
`provider.selected`, `provider.succeeded`, `provider.failed`,
`provider.timeout`, `provider.retry`, `provider.token_usage`,
`provider.latency`, `provider.estimated_cost` calls a `ProviderRecorder`
rather than the event bus directly. This indirection:

1. Decouples an adapter from the bus (one fewer collaborator to fake).
2. Centralises event publication so the vocabulary stays consistent.
3. Makes "silent failure if no bus" the universal rule -- a recorder
   constructed without an event bus absorbs all writes into memory so
   tests can inspect them, but in production it's just `self._bus.publish(...)`.

The recorder is *optional*. An adapter that wants nothing to do with
observability can ignore it entirely. Tool Manager's own
`tool_manager.*` events are still the canonical invocation timeline;
these provider.* events are a per-adapter, structured-predecessor of
those.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event


# Event type constants -- namespaced `tool_manager.provider.*` so
# existing observers on the wildcard can filter down or up.  Convention
# is `domain.entity.action` across Hermes; this lives in the
# Tool Manager namespace because the recorder is owned by an adapter.
PROVIDER_SELECTED = "tool_manager.provider.selected"
PROVIDER_SUCCEEDED = "tool_manager.provider.succeeded"
PROVIDER_FAILED = "tool_manager.provider.failed"
PROVIDER_TIMEOUT = "tool_manager.provider.timeout"
PROVIDER_RETRY = "tool_manager.provider.retry"
PROVIDER_TOKEN_USAGE = "tool_manager.provider.token_usage"
PROVIDER_LATENCY = "tool_manager.provider.latency"
PROVIDER_ESTIMATED_COST = "tool_manager.provider.estimated_cost"
PROVIDER_CANCELLED = "tool_manager.provider.cancelled"
PROVIDER_HEALTH_CHANGED = "tool_manager.provider.health_changed"


@dataclass
class ProviderEventLog:
    """In-memory ring of recent provider events, used by tests and by a
    future dashboard. Volatile -- lost on restart, by design."""

    max_size: int = 256
    events: list[dict[str, Any]] = field(default_factory=list)

    def append(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)
        if len(self.events) > self.max_size:
            del self.events[: max(0, len(self.events) - self.max_size)]

    def clear(self) -> None:
        self.events.clear()


class ProviderRecorder:
    """Owns the `provider.*` event vocabulary. A recorder without an
    event bus still holds events in `ProviderEventLog` so dry-run and
    test paths are observable."""

    SOURCE_MODULE = "tool_manager.provider"

    def __init__(self, *, event_bus: EventBus | None = None, log: ProviderEventLog | None = None) -> None:
        self._bus = event_bus
        self._log = log or ProviderEventLog()

    @property
    def log(self) -> ProviderEventLog:
        return self._log

    async def selected(
        self,
        *,
        provider: str,
        tool_name: str,
        capability: str,
        correlation_id: uuid.UUID | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        await self._publish(PROVIDER_SELECTED, provider, tool_name, capability, correlation_id, extra)

    async def succeeded(
        self,
        *,
        provider: str,
        tool_name: str,
        capability: str,
        correlation_id: uuid.UUID | None = None,
        latency_ms: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        if latency_ms is not None:
            payload["latency_ms"] = latency_ms
        if extra:
            payload.update(extra)
        await self._publish(PROVIDER_SUCCEEDED, provider, tool_name, capability, correlation_id, payload)

    async def failed(
        self,
        *,
        provider: str,
        tool_name: str,
        capability: str,
        correlation_id: uuid.UUID | None = None,
        error: str,
        retries: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"error": error, "retries": retries}
        if extra:
            payload.update(extra)
        await self._publish(PROVIDER_FAILED, provider, tool_name, capability, correlation_id, payload)

    async def timeout(
        self,
        *,
        provider: str,
        tool_name: str,
        capability: str,
        correlation_id: uuid.UUID | None = None,
        timeout_seconds: float,
    ) -> None:
        await self._publish(
            PROVIDER_TIMEOUT,
            provider,
            tool_name,
            capability,
            correlation_id,
            {"timeout_seconds": timeout_seconds},
        )

    async def retry(
        self,
        *,
        provider: str,
        tool_name: str,
        capability: str,
        correlation_id: uuid.UUID | None = None,
        attempt: int,
        reason: str,
        backoff_seconds: float,
    ) -> None:
        await self._publish(
            PROVIDER_RETRY,
            provider,
            tool_name,
            capability,
            correlation_id,
            {"attempt": attempt, "reason": reason, "backoff_seconds": backoff_seconds},
        )

    async def token_usage(
        self,
        *,
        provider: str,
        tool_name: str,
        capability: str,
        correlation_id: uuid.UUID | None = None,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        model: str | None = None,
    ) -> None:
        payload = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
        if model:
            payload["model"] = model
        await self._publish(PROVIDER_TOKEN_USAGE, provider, tool_name, capability, correlation_id, payload)

    async def latency(
        self,
        *,
        provider: str,
        tool_name: str,
        capability: str,
        correlation_id: uuid.UUID | None = None,
        latency_ms: float,
    ) -> None:
        await self._publish(
            PROVIDER_LATENCY, provider, tool_name, capability, correlation_id, {"latency_ms": latency_ms}
        )

    async def estimated_cost(
        self,
        *,
        provider: str,
        tool_name: str,
        capability: str,
        correlation_id: uuid.UUID | None = None,
        cost_usd: float,
    ) -> None:
        await self._publish(
            PROVIDER_ESTIMATED_COST,
            provider,
            tool_name,
            capability,
            correlation_id,
            {"cost_usd": cost_usd},
        )

    async def cancelled(
        self,
        *,
        provider: str,
        tool_name: str,
        capability: str,
        correlation_id: uuid.UUID | None = None,
    ) -> None:
        await self._publish(PROVIDER_CANCELLED, provider, tool_name, capability, correlation_id, {})

    async def health_changed(
        self,
        *,
        provider: str,
        tool_name: str,
        capability: str,
        correlation_id: uuid.UUID | None = None,
        state: str,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"state": state}
        if error:
            payload["error"] = error
        await self._publish(PROVIDER_HEALTH_CHANGED, provider, tool_name, capability, correlation_id, payload)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _publish(
        self,
        event_type: str,
        provider: str,
        tool_name: str,
        capability: str,
        correlation_id: uuid.UUID | None,
        payload: dict[str, Any],
    ) -> None:
        envelope: dict[str, Any] = {
            "provider": provider,
            "tool_name": tool_name,
            "capability": capability,
            **payload,
        }
        self._log.append({"event_type": event_type, **envelope})
        if self._bus is None:
            return
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module=self.SOURCE_MODULE,
                correlation_id=correlation_id or uuid.uuid4(),
                payload=envelope,
            )
        )


class Stopwatch:
    """Tiny context manager that records elapsed wall-clock latency in
    milliseconds. Used by the recorder above to populate
    `latency_ms`. Non-error: a non-context use just `start()`/`stop()`."""

    __slots__ = ("_start", "elapsed_ms")

    def __init__(self) -> None:
        self._start: float | None = None
        self.elapsed_ms: float = 0.0

    def start(self) -> None:
        self._start = time.perf_counter()

    def stop(self) -> float:
        if self._start is None:
            return 0.0
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        self._start = None
        return self.elapsed_ms

    def __enter__(self) -> "Stopwatch":
        self.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.stop()


__all__ = [
    "ProviderRecorder",
    "ProviderEventLog",
    "Stopwatch",
    "PROVIDER_SELECTED",
    "PROVIDER_SUCCEEDED",
    "PROVIDER_FAILED",
    "PROVIDER_TIMEOUT",
    "PROVIDER_RETRY",
    "PROVIDER_TOKEN_USAGE",
    "PROVIDER_LATENCY",
    "PROVIDER_ESTIMATED_COST",
    "PROVIDER_CANCELLED",
    "PROVIDER_HEALTH_CHANGED",
]
