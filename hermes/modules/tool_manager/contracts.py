"""Protocol every tool adapter must satisfy.

Extends `Supervisable` (core/supervisor/contracts.py) so any registered
adapter can be managed by the exact same Supervisor that manages every
other module -- Tool Manager implements no health-check loop or restart
logic of its own; it reuses the kernel's (see service.py).

Only `invoke` is universally required to do real work: a single request
in, a single result out is the one shape every provider -- an LLM API, a
notes vault, a future MCP server -- can express. `invoke_stream` is only
meaningful for adapters whose `capabilities.supports_streaming` is True;
Tool Manager checks that flag before calling it, and a non-streaming
adapter's default implementation raises `UnsupportedCapabilityError` as a
second line of defense.

"Asynchronous execution" (submit now, retrieve the result later) is
deliberately NOT part of this Protocol -- it's provided by Tool Manager
itself on top of `invoke()` (see `ToolManager.invoke_async`), so adapters
never need to implement their own job-submission machinery.
"""
from __future__ import annotations

from typing import AsyncIterator, Protocol

from hermes.core.supervisor.contracts import Supervisable
from hermes.modules.tool_manager.models import (
    ToolCapabilities,
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolStreamChunk,
)


class ToolAdapter(Supervisable, Protocol):
    name: str
    provider: str
    capabilities: ToolCapabilities

    async def authenticate(self) -> None:
        """Establishes/validates credentials. Tool Manager calls this
        once before an adapter's first `start()`, folding the two into
        one Supervisable lifecycle step -- see the internal shim in
        service.py. Raising is treated as a startup crash, handled by the
        Supervisor exactly like any other module failure."""
        ...

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        """The one operation every adapter must support: a single
        request in, a single result out."""
        ...

    def invoke_stream(self, request: ToolInvocationRequest) -> AsyncIterator[ToolStreamChunk]:
        """Only meaningful when `capabilities.supports_streaming` is
        True. Non-streaming adapters should raise
        `UnsupportedCapabilityError` when this is iterated."""
        ...
