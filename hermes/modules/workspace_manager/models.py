"""Pydantic data contracts for the Workspace Manager.

`Workspace` is the top-level record. Layout is broken into three
nested sub-records so each concern lives in its own type:
`LayoutState`, `WindowState`, `DockingState`. The shape of each
is stable across the workspace layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class WindowState(BaseModel):
    """State for one open window inside a workspace. The `bounds`
    field is intentionally a free-form dict so the future desktop
    UI can use any geometry convention (x/y/w/h, left/top/right/bottom,
    pixel/dip/percent, etc.)."""

    window_id: str
    application_id: str
    title: str
    bounds: dict[str, Any] = Field(default_factory=dict)
    is_focused: bool = False
    is_minimized: bool = False
    is_maximized: bool = False


class DockingState(BaseModel):
    """Docking surface: a named region that 0..N windows are docked
    into. The `dock_windows` list holds window_ids, not full
    `WindowState` objects, so the same window may be referenced by
    multiple docks (rare but legal) without duplication."""

    name: str
    region: Literal["left", "right", "top", "bottom", "center", "floating"]
    dock_windows: list[str] = Field(default_factory=list)


class LayoutState(BaseModel):
    """Aggregated layout for one workspace: every open window and
    every dock. The Manager exposes `get_layout_state(workspace_id)`
    which returns one of these."""

    workspace_id: uuid.UUID
    windows: list[WindowState] = Field(default_factory=list)
    docks: list[DockingState] = Field(default_factory=list)


class Workspace(BaseModel):
    """One workspace. Carries identity, ownership, layout, open
    missions / open projects / open applications, and timestamps.

    `current_application_id` is the "current application" pointer
    the directive names; the Session Manager reads/writes it.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    owner: str
    description: str = ""
    current_application_id: str | None = None
    open_mission_ids: list[uuid.UUID] = Field(default_factory=list)
    open_project_ids: list[uuid.UUID] = Field(default_factory=list)
    open_application_ids: list[str] = Field(default_factory=list)
    layout: LayoutState | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


__all__ = ["DockingState", "LayoutState", "WindowState", "Workspace"]
