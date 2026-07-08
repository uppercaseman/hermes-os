"""Pydantic data contracts for the Application Registry.

These types flow between the Registry, the Workspace Manager (for
`set_current_application` validation), the Session Manager (to
display the current application), and the event bus (for
`application.*` observability events).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ApplicationCategory = Literal[
    "mission_control",
    "memory",
    "developer",
    "dashboard",
    "knowledge",
    "automation",
    "provider",
    "settings",
    "custom",
]

ApplicationStatus = Literal["active", "inactive", "deprecated"]


class Application(BaseModel):
    """Metadata for one Hermes application. Stable shape across the
    workspace layer; consumers (Workspace Manager, Session Manager,
    future UI) read every field here and never invent their own."""

    id: str = Field(min_length=1, max_length=64)
    name: str
    description: str
    category: ApplicationCategory
    version: str = "0.0.0"
    route: str | None = None
    capabilities_required: list[str] = Field(default_factory=list)
    status: ApplicationStatus = "active"
    entrypoint_metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["Application", "ApplicationCategory", "ApplicationStatus"]
