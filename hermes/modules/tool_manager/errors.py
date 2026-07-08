"""Tool Manager-specific exception types.

Registry/lookup misuse (unknown tool name, duplicate registration) reuses
the plain built-in exceptions the Supervisor already uses for the same
kind of caller error (`ValueError` for "already registered", `KeyError`
for "not found" -- see core/supervisor/service.py's `_require`). These two
classes exist only for concepts that don't have a good built-in
equivalent.
"""
from __future__ import annotations

import uuid


class UnsupportedCapabilityError(Exception):
    """Raised when a caller asks an adapter to do something its declared
    `ToolCapabilities` says it can't (e.g. streaming from a sync-only
    adapter)."""

    def __init__(self, tool_name: str, capability: str) -> None:
        self.tool_name = tool_name
        self.capability = capability
        super().__init__(f"tool {tool_name!r} does not support {capability!r}")


class UnknownHandleError(Exception):
    """Raised by `get_result`/`await_result` for a handle that was never
    issued, or whose result was already retrieved."""

    def __init__(self, handle_id: uuid.UUID) -> None:
        self.handle_id = handle_id
        super().__init__(f"no pending invocation for handle {handle_id}")
