"""Workspace Manager Protocol contracts.

Defines the two surfaces every module depends on:

- `WorkspaceManagerProtocol` -- the read/write surface every
  consumer (Session Manager) uses.
- `WorkspaceStore` -- the persistence Protocol. The Manager calls
  this on `save_workspace(...)`. Tests pass `InMemoryWorkspaceStore`;
  production callers pass `JsonFileStore` (defined in service.py).

Both are `runtime_checkable`.
"""
from __future__ import annotations

import uuid
from typing import Any, Protocol, runtime_checkable

from hermes.modules.workspace_manager.models import (
    LayoutState,
    Workspace,
)


@runtime_checkable
class WorkspaceStore(Protocol):
    """Persistence Protocol for one or more `Workspace` records.

    A `WorkspaceStore` is the single seam the workspace layer uses
    to plug a different backend in (JSON-on-disk, SQLite, Postgres,
    cloud KV, etc.). Implementations MUST be safe to call multiple
    times -- the Manager calls `save(...)` explicitly, never
    implicitly."""

    async def save(self, workspace: Workspace) -> None:
        ...

    async def load(self, workspace_id: uuid.UUID) -> Workspace | None:
        ...

    async def list_ids(self) -> list[uuid.UUID]:
        ...


@runtime_checkable
class WorkspaceManagerProtocol(Protocol):
    """Read/write surface every consumer of the Workspace Manager uses.

    `SessionManager` uses this Protocol so it never imports the
    Manager's concrete class."""

    async def create_workspace(
        self, *, name: str, owner: str, description: str = ""
    ) -> Workspace:
        ...

    async def get_workspace(self, workspace_id: uuid.UUID) -> Workspace | None:
        ...

    async def list_workspaces(self) -> list[Workspace]:
        ...

    async def set_current_workspace(self, workspace_id: uuid.UUID) -> Workspace:
        ...

    def get_current_workspace(self) -> Workspace | None:
        ...

    async def set_current_application(
        self, workspace_id: uuid.UUID, application_id: str
    ) -> Workspace:
        ...

    async def open_mission(
        self, workspace_id: uuid.UUID, mission_id: uuid.UUID
    ) -> Workspace:
        ...

    async def close_mission(
        self, workspace_id: uuid.UUID, mission_id: uuid.UUID
    ) -> Workspace:
        ...

    def get_layout_state(self, workspace_id: uuid.UUID) -> LayoutState | None:
        ...

    def snapshot_layout(
        self, workspace_id: uuid.UUID, layout: LayoutState
    ) -> LayoutState:
        ...

    async def save_workspace(self, workspace_id: uuid.UUID) -> Workspace:
        ...

    async def restore_workspace(self, workspace_id: uuid.UUID) -> Workspace | None:
        ...


__all__ = ["WorkspaceManagerProtocol", "WorkspaceStore"]
