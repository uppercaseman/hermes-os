"""Session Manager-specific exception types."""
from __future__ import annotations

import uuid


class SessionManagerError(Exception):
    """Base for session-level errors."""


class UnknownSessionError(SessionManagerError):
    def __init__(self, session_id: uuid.UUID) -> None:
        self.session_id = session_id
        super().__init__(f"session {session_id!s} is not active")


class UnknownWorkspaceReferenceError(SessionManagerError):
    """Raised when `set_current_workspace` is called with a workspace
    id that the Workspace Manager does not know about."""

    def __init__(self, workspace_id: uuid.UUID) -> None:
        self.workspace_id = workspace_id
        super().__init__(
            f"workspace {workspace_id!s} is not registered"
        )


class SessionConfigError(SessionManagerError):
    """Construction-time configuration error."""


__all__ = [
    "SessionManagerError",
    "UnknownSessionError",
    "UnknownWorkspaceReferenceError",
    "SessionConfigError",
]