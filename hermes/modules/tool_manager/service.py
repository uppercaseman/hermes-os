"""Tool Manager -- the only path from Hermes to external systems.

Hermes Commander (and every other module) never talks to an API directly;
every external service is represented by a `ToolAdapter` registered here.
Tool Manager itself contains zero provider-specific logic -- everything in
this file is generic orchestration:

- Retries: reuses `RetryPolicy` (core/supervisor/policy.py) -- the same
  building block already used for task retry (Commander) and module
  restart (Supervisor).
- Rate limits: one `RateLimiter` per registered adapter (rate_limiter.py).
- Authentication: `adapter.authenticate()` is folded into that adapter's
  Supervisor-managed startup (see `_SupervisableAdapter` below).
- Health monitoring + automatic restart: delegated entirely to the same
  `Supervisor` that (will) manage every other module -- an adapter is
  just another `Supervisable` unit from the Supervisor's point of view.
- Sync execution: `invoke()` awaits the full result inline.
- Async execution: `invoke_async()` schedules `invoke()` as a background
  task and returns a handle immediately; `get_result`/`await_result`
  retrieve it later. This is a Tool Manager capability, not something
  each adapter must implement.
- Streaming: `invoke_stream()` is a pass-through to an adapter's own
  `invoke_stream`, gated by its declared capability, with retry applied
  only to stream *establishment* (getting the first chunk) -- a
  mid-stream failure is terminal and reported as a final error chunk,
  never retried silently.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.core.supervisor.interface import Supervisor, SupervisedUnitConfig, build_supervisor
from hermes.modules.configuration_manager.interface import ConfigurationManager
from hermes.modules.tool_manager import events as evt
from hermes.modules.tool_manager.contracts import ToolAdapter
from hermes.modules.tool_manager.errors import UnknownHandleError, UnsupportedCapabilityError
from hermes.modules.tool_manager.models import (
    ToolAdapterConfig,
    ToolInvocationHandle,
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolStatus,
    ToolStreamChunk,
)
from hermes.modules.tool_manager.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

SOURCE_MODULE = "tool_manager"


@dataclass
class _AdapterRecord:
    adapter: ToolAdapter
    config: ToolAdapterConfig
    rate_limiter: RateLimiter
    total_invocations: int = 0
    total_failures: int = 0


class _SupervisableAdapter:
    """Bridges the richer `ToolAdapter` protocol to `Supervisable`,
    folding `authenticate()` into `start()` so the Supervisor's lifecycle/
    health/restart machinery can manage an adapter without knowing
    anything about tool-specific concerns."""

    def __init__(self, adapter: ToolAdapter) -> None:
        self._adapter = adapter

    async def start(self) -> None:
        await self._adapter.authenticate()
        await self._adapter.start()

    async def stop(self) -> None:
        await self._adapter.stop()

    async def health_check(self) -> bool:
        return await self._adapter.health_check()


class ToolManager:
    """Registry, invocation, and supervision point for every tool adapter
    Hermes uses. See the module docstring for what each capability maps
    to."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        supervisor: Supervisor | None = None,
        configuration_manager: ConfigurationManager | None = None,
    ) -> None:
        """If `supervisor` is omitted, Tool Manager builds its own bound
        to the same event bus, so every registered adapter is always
        health-monitored and auto-restarted -- there is no "unsupervised"
        mode.

        `configuration_manager` is entirely optional and purely additive:
        omitting it (the default) reproduces every prior behavior of this
        class exactly, since `register_adapter()` itself is unchanged and
        still requires a caller-built `ToolAdapterConfig`. When given, it
        only powers `default_adapter_config()` below -- nothing here reads
        from it automatically or otherwise changes what already worked."""
        self._bus = event_bus
        self._supervisor = supervisor or build_supervisor(event_bus=event_bus)
        self._configuration_manager = configuration_manager
        self._adapters: dict[str, _AdapterRecord] = {}
        self._pending: dict[uuid.UUID, asyncio.Task[ToolInvocationResult]] = {}

    def default_adapter_config(self, name: str) -> ToolAdapterConfig:
        """Builds a `ToolAdapterConfig` for `name`, sourcing
        `invocation_timeout_seconds`/`health_check_interval_seconds` from
        the `tool_manager.*` namespace of `configuration_manager` if one
        was given -- with `ToolAdapterConfig`'s own pydantic field
        defaults as the fallback for each, exactly as if no Configuration
        Manager existed at all. `rate_limit`/`retry_policy`/`auth` are
        nested models this method deliberately does not source from
        config yet (see the Tool Manager README's "known gaps") --
        callers needing those still build a `ToolAdapterConfig` directly.

        With no `configuration_manager` (the default), or one with
        nothing set under `tool_manager.*`, this returns exactly
        `ToolAdapterConfig(name=name)` -- byte-for-byte the same object
        `register_adapter()` callers have always been able to construct
        themselves."""
        timeout_field = ToolAdapterConfig.model_fields["invocation_timeout_seconds"]
        health_field = ToolAdapterConfig.model_fields["health_check_interval_seconds"]
        if self._configuration_manager is None:
            return ToolAdapterConfig(name=name)
        return ToolAdapterConfig(
            name=name,
            invocation_timeout_seconds=self._configuration_manager.get(
                "tool_manager.invocation_timeout_seconds", timeout_field.get_default()
            ),
            health_check_interval_seconds=self._configuration_manager.get(
                "tool_manager.health_check_interval_seconds", health_field.get_default()
            ),
        )

    def register_adapter(self, adapter: ToolAdapter, config: ToolAdapterConfig) -> None:
        """Registers `adapter` under `config.name` and enrolls it with
        the Supervisor for health monitoring and automatic restart. Does
        not start it -- call `start_all()` / a future `start(name)` once
        every adapter you want is registered.

        Raises `ValueError` if `config.name` is already registered.
        """
        if config.name in self._adapters:
            raise ValueError(f"a tool named {config.name!r} is already registered")
        self._adapters[config.name] = _AdapterRecord(
            adapter=adapter,
            config=config,
            rate_limiter=RateLimiter(config.rate_limit),
        )
        self._supervisor.register(
            _SupervisableAdapter(adapter),
            SupervisedUnitConfig(
                name=config.name,
                restart_strategy="permanent",
                retry_policy=config.retry_policy,
                health_check_interval_seconds=config.health_check_interval_seconds,
            ),
        )

    async def start_all(self) -> None:
        """Authenticates and starts every registered adapter via the
        Supervisor, which then owns health monitoring and restart for
        each of them."""
        await self._supervisor.start_all()

    async def stop_all(self) -> None:
        await self._supervisor.stop_all()

    async def status(self, name: str) -> ToolStatus:
        record = self._require(name)
        unit_status = await self._supervisor.status(name)
        return ToolStatus(
            name=name,
            provider=record.adapter.provider,
            state=unit_status.state,
            capabilities=record.adapter.capabilities,
            total_invocations=record.total_invocations,
            total_failures=record.total_failures,
        )

    async def status_all(self) -> list[ToolStatus]:
        return [await self.status(name) for name in self._adapters]

    # ------------------------------------------------------------------ #
    # Synchronous execution
    # ------------------------------------------------------------------ #
    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        """Awaits the full result inline, applying this adapter's rate
        limit, timeout, and retry policy. Never raises for the adapter's
        own failure -- returns a `status="failed"` result instead."""
        record = self._require(request.tool_name)
        await record.rate_limiter.acquire()

        attempt = 1
        while True:
            try:
                result = await asyncio.wait_for(
                    record.adapter.invoke(request), timeout=record.config.invocation_timeout_seconds
                )
                result.attempts = attempt
                record.total_invocations += 1
                await self._publish(evt.TOOL_INVOKED, request, {"attempt": attempt})
                return result
            except Exception as exc:  # noqa: BLE001 -- an adapter's failure is
                # data for the retry policy, never a reason to raise past
                # this boundary.
                record.total_invocations += 1
                record.total_failures += 1
                if not record.config.retry_policy.should_retry(attempt, record.config.retry_policy.max_attempts):
                    await self._publish(
                        evt.TOOL_INVOCATION_FAILED, request, {"attempt": attempt, "error": str(exc)}
                    )
                    return ToolInvocationResult(
                        tool_name=request.tool_name,
                        correlation_id=request.correlation_id,
                        status="failed",
                        error=str(exc),
                        attempts=attempt,
                    )
                backoff = record.config.retry_policy.next_backoff(attempt)
                await self._publish(
                    evt.TOOL_RETRY_SCHEDULED, request, {"attempt": attempt, "backoff_seconds": backoff}
                )
                if backoff > 0:
                    await asyncio.sleep(backoff)
                attempt += 1

    # ------------------------------------------------------------------ #
    # Asynchronous execution -- built on top of invoke(), not the adapter
    # ------------------------------------------------------------------ #
    async def invoke_async(self, request: ToolInvocationRequest) -> ToolInvocationHandle:
        """Schedules `invoke()` as a background task and returns a handle
        immediately. No adapter needs to implement its own async
        submission protocol -- this wraps any adapter uniformly."""
        self._require(request.tool_name)  # fail fast on an unknown tool
        handle = ToolInvocationHandle(tool_name=request.tool_name, correlation_id=request.correlation_id)
        self._pending[handle.handle_id] = asyncio.ensure_future(self.invoke(request))
        return handle

    async def get_result(self, handle: ToolInvocationHandle) -> ToolInvocationResult | None:
        """Non-blocking poll: `None` if still pending, the result once
        available (after which the handle is consumed)."""
        task = self._pending.get(handle.handle_id)
        if task is None:
            raise UnknownHandleError(handle.handle_id)
        if not task.done():
            return None
        del self._pending[handle.handle_id]
        return task.result()

    async def await_result(
        self, handle: ToolInvocationHandle, timeout: float | None = None
    ) -> ToolInvocationResult:
        """Blocking wait for the same handle. Uses `asyncio.shield` so a
        `timeout` here only stops this waiter, not the underlying
        invocation -- a later `get_result`/`await_result` can still
        retrieve it."""
        task = self._pending.get(handle.handle_id)
        if task is None:
            raise UnknownHandleError(handle.handle_id)
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        finally:
            if task.done():
                self._pending.pop(handle.handle_id, None)
        return result

    # ------------------------------------------------------------------ #
    # Streaming execution
    # ------------------------------------------------------------------ #
    async def invoke_stream(self, request: ToolInvocationRequest) -> AsyncIterator[ToolStreamChunk]:
        """Retries only stream *establishment* (getting the first chunk).
        Once streaming has begun, a failure ends the stream with a final
        chunk carrying `error` set -- never retried mid-stream, and never
        raised."""
        record = self._require(request.tool_name)
        if not record.adapter.capabilities.supports_streaming:
            raise UnsupportedCapabilityError(request.tool_name, "streaming")
        await record.rate_limiter.acquire()

        attempt = 1
        while True:
            stream = record.adapter.invoke_stream(request)
            try:
                first_chunk = await asyncio.wait_for(
                    stream.__anext__(), timeout=record.config.invocation_timeout_seconds
                )
                break
            except StopAsyncIteration:
                record.total_invocations += 1
                return  # an empty but successful stream
            except Exception as exc:  # noqa: BLE001 -- setup failure, retryable
                await stream.aclose()
                record.total_invocations += 1
                record.total_failures += 1
                if not record.config.retry_policy.should_retry(attempt, record.config.retry_policy.max_attempts):
                    await self._publish(evt.TOOL_STREAM_FAILED, request, {"attempt": attempt, "error": str(exc)})
                    yield ToolStreamChunk(
                        tool_name=request.tool_name,
                        correlation_id=request.correlation_id,
                        sequence=0,
                        is_final=True,
                        error=str(exc),
                    )
                    return
                backoff = record.config.retry_policy.next_backoff(attempt)
                if backoff > 0:
                    await asyncio.sleep(backoff)
                attempt += 1

        yield first_chunk
        if not first_chunk.is_final:
            async for chunk in stream:
                yield chunk

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _require(self, name: str) -> _AdapterRecord:
        if name not in self._adapters:
            raise KeyError(f"no tool adapter named {name!r} is registered")
        return self._adapters[name]

    async def _publish(self, event_type: str, request: ToolInvocationRequest, payload: dict[str, Any]) -> None:
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module=SOURCE_MODULE,
                correlation_id=request.correlation_id,
                payload={"tool_name": request.tool_name, **payload},
            )
        )
