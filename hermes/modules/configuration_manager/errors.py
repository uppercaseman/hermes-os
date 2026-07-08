"""Errors the Configuration Manager raises.

Follows the OS-wide rule: raise only for caller misuse (an unregistered
namespace, a schema the merged config doesn't satisfy); every read
method that can legitimately come up empty (`get`, `get_provider_config`,
`is_feature_enabled`) returns a default instead.
"""
from __future__ import annotations

from typing import Any


class UnknownNamespaceError(Exception):
    """Raised by `get_module_config()` when no schema was ever
    registered for the requested namespace -- asking for a validated
    view of config nothing declared a shape for is a caller error, not
    a runtime condition to tolerate silently."""

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        super().__init__(f"no schema registered for namespace {namespace!r}")


class ConfigValidationError(Exception):
    """Raised when the merged configuration for a namespace does not
    satisfy the schema registered for it -- either at `register_schema`
    time (fail fast) or any later `get_module_config()` call (e.g.
    after a `reload()` picked up a bad value)."""

    def __init__(self, namespace: str, errors: list[dict[str, Any]]) -> None:
        self.namespace = namespace
        self.errors = errors
        super().__init__(f"configuration for namespace {namespace!r} failed validation: {errors}")
