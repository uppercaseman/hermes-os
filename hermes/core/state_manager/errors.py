"""State Manager-specific exception types."""
from __future__ import annotations


class UnknownModuleError(Exception):
    """Raised by a query for a module that has never been declared and
    has never reported a heartbeat -- distinct from a known module that
    simply hasn't reported in (which reads as "offline", not an error)."""

    def __init__(self, module_name: str) -> None:
        self.module_name = module_name
        super().__init__(f"module {module_name!r} has never been declared or reported a heartbeat")
