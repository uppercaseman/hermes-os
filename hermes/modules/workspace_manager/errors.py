"""Workspace Manager-specific exception types.

- `UnknownWorkspaceError`: lookup of a workspace id that does not
  exist (or was deleted).
- `WorkspaceConfigError`: configuration / construction-time error
  (e.g. contradictory `store` + `application_registry`).
- `WorkspaceManagerError`: base class.
"""
from __future__ import annotations

import uuid


class WorkspaceManagerError(Exception):
    """Base for workspace-level errors."""


class UnknownWorkspaceError(WorkspaceManagerError):
    def __init__(self, workspace_id: uuid.UUID) -> None:
        self.workspace_id = workspace_id
        super().__init__(f"workspace {workspace_id!s} is not registered")


class WorkspaceConfigError(WorkspaceManagerError):
    """Raised when workspace configuration is contradictory or invalid."""


__all__ = [
    "UnknownWorkspaceError",
    "WorkspaceConfigError",
    "WorkspaceManagerError",
]
