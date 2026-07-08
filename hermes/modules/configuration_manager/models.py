"""Pydantic data contracts for the Configuration Manager."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

ConfigSource = Literal["default", "file", "env", "override"]


class ConfigEntry(BaseModel):
    """One effective configuration value, for dashboard/diagnostic
    display -- `value` has already been passed through redaction, so
    this is always safe to serialize and display as-is."""

    path: str
    value: Any
    source: ConfigSource
    namespace_validated: bool = Field(
        description="True if `path` falls under a namespace with a registered schema."
    )


class ConfigSnapshot(BaseModel):
    """A dashboard-ready, JSON-serializable snapshot of the entire
    effective configuration -- the whole of "future UI/dashboard editing
    support" today, exactly like State Manager's `SystemDiagnostics`: a
    future HTTP endpoint serves `.model_dump()` of this directly."""

    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    entries: list[ConfigEntry]
    feature_flags: dict[str, bool]
    registered_namespaces: list[str]
