"""'Paperclip' placeholder adapter.

Kept as a dry-run-only stub for the not-yet-finalised third-party
service. Production-ready in shape, but `invoke()` simply returns a
structured dry-run result so the adapter is safe to construct and
register in any environment. Wire a real implementation when the
provider's contract is locked.
"""
from __future__ import annotations

from typing import AsyncIterator

from hermes.modules.tool_manager.adapters.base import BaseToolAdapter
from hermes.modules.tool_manager.adapters.provider_events import (
    ProviderEventLog,
    ProviderRecorder,
)
from hermes.modules.tool_manager.models import (
    ToolCapabilities,
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolStreamChunk,
)


PROVIDER_NAME = "paperclip"
SUPPORTED_CAPABILITIES: tuple[str, ...] = ()


class PaperclipAdapter(BaseToolAdapter):
    provider = PROVIDER_NAME
    capabilities = ToolCapabilities(supports_sync=True, supports_streaming=False, requires_auth=True)

    def __init__(
        self,
        *,
        name: str,
        dry_run: bool = True,
        invocation_timeout_seconds: float = 30.0,
        max_retries: int = 0,
        recorder: ProviderRecorder | None = None,
    ) -> None:
        super().__init__(name=name)
        self.dry_run = dry_run
        self._timeout = invocation_timeout_seconds
        self._max_retries = max_retries
        self._recorder = recorder if recorder is not None else ProviderRecorder(log=ProviderEventLog())

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        capability = str(request.parameters.get("capability", "communication"))
        return ToolInvocationResult(
            tool_name=request.tool_name,
            correlation_id=request.correlation_id,
            status="completed",
            output={
                "dry_run": True,
                "provider": self.provider,
                "capability": capability,
                "operation": request.operation,
                "echo_parameters": request.parameters,
                "note": "Paperclip integration is a stub; replace this adapter with a real one when the provider's contract is finalised.",
            },
        )

    async def invoke_stream(self, request: ToolInvocationRequest) -> AsyncIterator[ToolStreamChunk]:
        # Paperclip is declared non-streaming; surfaces a clear error chunk
        # rather than spinning up any I/O.
        yield ToolStreamChunk(
            tool_name=request.tool_name,
            correlation_id=request.correlation_id,
            sequence=0,
            is_final=True,
            error="paperclip does not support streaming",
        )


__all__ = ["PaperclipAdapter", "PROVIDER_NAME", "SUPPORTED_CAPABILITIES"]
