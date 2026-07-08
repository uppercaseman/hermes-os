"""Session Manager Protocol contracts.

Defines the surfaces this module exposes:

- `WorkspaceAccessor` -- the narrow Protocol the Session Manager
  uses to resolve a `workspace_id`. Anything that has
  `async def get_workspace(self, workspace_id) -> Workspace | None`
  satisfies it; in practice this is the `WorkspaceManager` but
  tests can substitute any object with the right shape.

- `SessionStore` -- persistence Protocol. Implementations live
  in `service.py`; tests pass `InMemorySessionStore`.

- `SessionManagerProtocol` -- the full surface the future UI
  consumes. Matches every method exposed by `SessionManager`.
"""
from __future__ import annotations

import uuid
from typing import Any, Protocol, runtime_checkable

from hermes.modules.session_manager.models import WorkspaceSession
from hermes.modules.workspace_manager.models import Workspace


@runtime_checkable
class WorkspaceAccessor(Protocol):
    """Narrow Protocol that captures the surface Session Manager
    needs from the Workspace Manager. The Workspace Manager's
    concrete class satisfies this implicitly."""

    async def get_workspace(
        self, workspace_id: uuid.UUID
    ) -> Workspace | None:
        ...


@runtime_checkable
class SessionStore(Protocol):
    """Persistence Protocol for `WorkspaceSession` records.

    Optional -- the default in-memory store is used when none is
    passed. A cloud-flavored `SessionStore` (Redis, Postgres, ...)
    plugs in here."""

    async def save(self, session: WorkspaceSession) -> None:
        ...

    async def load(self, session_id: uuid.UUID) -> WorkspaceSession | None:
        ...

    async def list_ids(self) -> list[uuid.UUID]:
        ...


@runtime_checkable
class SessionManagerProtocol(Protocol):
    async def start_session(self, *, user_id: str) -> WorkspaceSession:
        ...

    async def end_session(self, session_id: uuid.UUID) -> WorkspaceSession:
        ...

    async def get_session(
        self, session_id: uuid.UUID
    ) -> WorkspaceSession | None:
        ...

    async def set_current_workspace(
        self, session_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> WorkspaceSession:
        ...

    async def set_current_application(
        self, session_id: uuid.UUID, application_id: str
    ) -> WorkspaceSession:
        ...

    async def set_current_mission(
        self, session_id: uuid.UUID, mission_id: uuid.UUID
    ) -> WorkspaceSession:
        ...

    async def set_current_project(
        self, session_id: uuid.UUID, project_id: uuid.UUID
    ) -> WorkspaceSession:
        ...

    def recent_activity(
        self, session_id: uuid.UUID, *, limit: int = 20
    ) -> list[Any]:
        ...

    async def persist(self, session_id: uuid.UUID) -> WorkspaceSession:
        ...

    async def restore(self, session_id: uuid.UUID) -> WorkspaceSession | None:
        ...


__all__ = [
    "SessionManagerProtocol",
    "SessionStore",
    "WorkspaceAccessor",
]
