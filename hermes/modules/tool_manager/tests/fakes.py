"""Test double satisfying the ToolAdapter protocol.

NOT a real provider integration -- a scripted stand-in used only to
exercise Tool Manager's own orchestration logic (retries, rate limits,
streaming, async) in isolation, mirroring the ScriptedUnit /
ScriptedTaskDispatcher pattern used by the Supervisor and Commander tests.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from hermes.modules.tool_manager.models import (
    ToolCapabilities,
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolStreamChunk,
)


class ScriptedToolAdapter:
    def __init__(
        self,
        *,
        name: str,
        provider: str = "fake",
        capabilities: ToolCapabilities | None = None,
        invoke_outcomes: list[str] | None = None,
        stream_setup_outcomes: list[str] | None = None,
        stream_chunks: list[dict[str, Any]] | None = None,
    ) -> None:
        self.name = name
        self.provider = provider
        self.capabilities = capabilities or ToolCapabilities(supports_streaming=stream_chunks is not None)
        self._invoke_outcomes = list(invoke_outcomes) if invoke_outcomes is not None else ["ok"]
        self._stream_setup_outcomes = (
            list(stream_setup_outcomes) if stream_setup_outcomes is not None else ["ok"]
        )
        self._stream_chunks = stream_chunks or []

        self.authenticate_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.health_calls = 0
        self.invoke_calls = 0

    async def authenticate(self) -> None:
        self.authenticate_calls += 1

    async def start(self) -> None:
        self.start_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1

    async def health_check(self) -> bool:
        self.health_calls += 1
        return True

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        self.invoke_calls += 1
        if self._next(self._invoke_outcomes) == "raise":
            raise RuntimeError("scripted invocation failure")
        return ToolInvocationResult(
            tool_name=request.tool_name,
            correlation_id=request.correlation_id,
            status="completed",
            output={"echo": request.operation},
        )

    async def invoke_stream(self, request: ToolInvocationRequest) -> AsyncIterator[ToolStreamChunk]:
        if self._next(self._stream_setup_outcomes) == "raise":
            raise RuntimeError("scripted stream setup failure")
        for i, data in enumerate(self._stream_chunks):
            yield ToolStreamChunk(
                tool_name=request.tool_name,
                correlation_id=request.correlation_id,
                sequence=i,
                data=data,
                is_final=(i == len(self._stream_chunks) - 1),
            )

    @staticmethod
    def _next(outcomes: list[str]) -> str:
        if len(outcomes) > 1:
            return outcomes.pop(0)
        return outcomes[0]
