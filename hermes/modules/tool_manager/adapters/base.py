"""Common scaffolding every provider-specific adapter builds on.

Concrete provider integrations are NOT built here or in any of the
sibling adapter files -- only placeholder skeletons. This class exists so
every adapter shares identical lifecycle/capability-check behavior and
only needs to override `invoke` (the one method every adapter must
actually implement) and, optionally, `invoke_stream`.
"""
from __future__ import annotations

from typing import AsyncIterator

from hermes.modules.tool_manager.errors import UnsupportedCapabilityError
from hermes.modules.tool_manager.models import (
    ToolCapabilities,
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolStreamChunk,
)


class BaseToolAdapter:
    """Default, provider-agnostic lifecycle and capability-check
    behavior. Subclasses set `provider`/`capabilities` as class
    attributes and override `invoke` (required) and `invoke_stream`
    (only if `capabilities.supports_streaming` is True)."""

    provider: str = "unknown"
    capabilities: ToolCapabilities = ToolCapabilities()

    def __init__(self, *, name: str) -> None:
        self.name = name

    async def authenticate(self) -> None:
        """Placeholder: a real adapter validates credentials here (using
        the `AuthConfig.credential_ref` it was registered with). Default
        is a no-op success."""
        return None

    async def start(self) -> None:
        """Placeholder lifecycle hook -- see `Supervisable` in
        core/supervisor/contracts.py. Default is a no-op success."""
        return None

    async def stop(self) -> None:
        return None

    async def health_check(self) -> bool:
        """Placeholder: always healthy. A real adapter would ping the
        provider (e.g. a lightweight status endpoint)."""
        return True

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        raise NotImplementedError(
            f"{self.__class__.__name__} is an infrastructure placeholder -- "
            f"provider-specific invocation is not implemented."
        )

    async def invoke_stream(self, request: ToolInvocationRequest) -> AsyncIterator[ToolStreamChunk]:
        if not self.capabilities.supports_streaming:
            raise UnsupportedCapabilityError(self.name, "streaming")
        raise NotImplementedError(
            f"{self.__class__.__name__} declares streaming support but invoke_stream "
            f"is not implemented."
        )
        yield  # pragma: no cover -- unreachable; keeps this an async generator
