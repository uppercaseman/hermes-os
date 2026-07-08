"""Session Manager service.

Owns one or more `WorkspaceSession` records. Each session carries
the current-{workspace, application, mission, project, user}
pointers and a bounded recent-activity ring. The Manager depends
on `WorkspaceAccessor` (a Protocol) to validate `set_current_workspace`
calls -- when the Workspace Manager is present it is consulted; when
absent the call accepts any UUID.

Key properties:

- **One session per login.** The Manager has no concept of a
  "global" session; every session is keyed by its UUID. A typical
  desktop UI calls `start_session(user_id=...)` on launch and
  `end_session(session_id)` on shutdown.
- **Idempotent current-X.** Each `set_current_*` only fires a
  `session.current_*_changed` event when the pointer actually
  changes.
- **Recent-activity ring.** Capped at
  `recent_activity_capacity` (default 50). Oldest-first eviction.
- **Optional persistence.** Without a `SessionStore`, sessions
  live in memory and vanish on restart. With one, `persist(...)`
  is explicit -- no implicit writes happen.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.session_manager import events as evt
from hermes.modules.session_manager.contracts import (
    SessionStore,
    WorkspaceAccessor,
)
from hermes.modules.session_manager.errors import (
    UnknownSessionError,
    UnknownWorkspaceReferenceError,
)
from hermes.modules.session_manager.models import (
    ActivityKind,
    RecentActivity,
    WorkspaceSession,
)

SOURCE_MODULE = "session_manager"


# ---------------------------------------------------------------------- #
# Default in-memory store
# ---------------------------------------------------------------------- #
class InMemorySessionStore:
    def __init__(self) -> None:
        self._records: dict[uuid.UUID, WorkspaceSession] = {}

    async def save(self, session: WorkspaceSession) -> None:
        self._records[session.id] = session.model_copy(deep=True)

    async def load(self, session_id: uuid.UUID) -> WorkspaceSession | None:
        record = self._records.get(session_id)
        if record is None:
            return None
        return record.model_copy(deep=True)

    async def list_ids(self) -> list[uuid.UUID]:
        return list(self._records.keys())


# ---------------------------------------------------------------------- #
# Manager
# ---------------------------------------------------------------------- #
class SessionManager:
    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        workspace_manager: WorkspaceAccessor | None = None,
        recent_activity_capacity: int = 50,
        clock: Optional[Callable[[], datetime]] = None,
        store: SessionStore | None = None,
    ) -> None:
        if recent_activity_capacity < 1:
            raise ValueError("recent_activity_capacity must be >= 1")
        self._bus = event_bus
        self._workspace = workspace_manager
        self._capacity = recent_activity_capacity
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._store = store or InMemorySessionStore()
        self._sessions: dict[uuid.UUID, WorkspaceSession] = {}

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start_session(self, *, user_id: str) -> WorkspaceSession:
        if not user_id:
            raise ValueError("user_id is required")
        now = self._clock()
        session = WorkspaceSession(user_id=user_id, started_at=now)
        self._sessions[session.id] = session
        self._append_activity(
            session.id, ActivityKind.SESSION_STARTED, subject=user_id
        )
        await self._publish(
            evt.SESSION_STARTED,
            {"session_id": str(session.id), "user_id": user_id},
        )
        return session.model_copy(deep=True)

    async def end_session(self, session_id: uuid.UUID) -> WorkspaceSession:
        if session_id not in self._sessions:
            raise UnknownSessionError(session_id)
        record = self._sessions[session_id]
        ended = record.model_copy(update={"ended_at": self._clock()})
        self._sessions[session_id] = ended
        self._append_activity(
            session_id, ActivityKind.SESSION_ENDED, subject=ended.user_id
        )
        await self._publish(
            evt.SESSION_ENDED,
            {"session_id": str(session_id), "user_id": ended.user_id},
        )
        return ended.model_copy(deep=True)

    async def get_session(
        self, session_id: uuid.UUID
    ) -> WorkspaceSession | None:
        record = self._sessions.get(session_id)
        return record.model_copy(deep=True) if record is not None else None

    async def list_sessions(self) -> list[WorkspaceSession]:
        return [s.model_copy(deep=True) for s in self._sessions.values()]

    # ------------------------------------------------------------------ #
    # Current-X pointers
    # ------------------------------------------------------------------ #
    async def set_current_workspace(
        self,
        session_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> WorkspaceSession:
        if session_id not in self._sessions:
            raise UnknownSessionError(session_id)
        if self._workspace is not None:
            record = await self._workspace.get_workspace(workspace_id)
            if record is None:
                raise UnknownWorkspaceReferenceError(workspace_id)
        session = self._sessions[session_id]
        if session.current_workspace_id == workspace_id:
            return session.model_copy(deep=True)
        updated = session.model_copy(
            update={"current_workspace_id": workspace_id}
        )
        self._sessions[session_id] = updated
        self._append_activity(
            session_id,
            ActivityKind.WORKSPACE_CHANGED,
            subject=str(workspace_id),
        )
        await self._publish(
            evt.SESSION_CURRENT_WORKSPACE_CHANGED,
            {
                "session_id": str(session_id),
                "workspace_id": str(workspace_id),
                "previous_workspace_id": (
                    str(session.current_workspace_id)
                    if session.current_workspace_id is not None
                    else None
                ),
            },
        )
        return updated.model_copy(deep=True)

    async def set_current_application(
        self,
        session_id: uuid.UUID,
        application_id: str,
    ) -> WorkspaceSession:
        if session_id not in self._sessions:
            raise UnknownSessionError(session_id)
        session = self._sessions[session_id]
        if session.current_application_id == application_id:
            return session.model_copy(deep=True)
        updated = session.model_copy(
            update={"current_application_id": application_id}
        )
        self._sessions[session_id] = updated
        self._append_activity(
            session_id,
            ActivityKind.APPLICATION_CHANGED,
            subject=application_id,
        )
        await self._publish(
            evt.SESSION_CURRENT_APPLICATION_CHANGED,
            {
                "session_id": str(session_id),
                "application_id": application_id,
                "previous_application_id": session.current_application_id,
            },
        )
        return updated.model_copy(deep=True)

    async def set_current_mission(
        self,
        session_id: uuid.UUID,
        mission_id: uuid.UUID,
    ) -> WorkspaceSession:
        if session_id not in self._sessions:
            raise UnknownSessionError(session_id)
        session = self._sessions[session_id]
        if session.current_mission_id == mission_id:
            return session.model_copy(deep=True)
        updated = session.model_copy(
            update={"current_mission_id": mission_id}
        )
        self._sessions[session_id] = updated
        self._append_activity(
            session_id,
            ActivityKind.MISSION_CHANGED,
            subject=str(mission_id),
        )
        await self._publish(
            evt.SESSION_CURRENT_MISSION_CHANGED,
            {
                "session_id": str(session_id),
                "mission_id": str(mission_id),
                "previous_mission_id": (
                    str(session.current_mission_id)
                    if session.current_mission_id is not None
                    else None
                ),
            },
        )
        return updated.model_copy(deep=True)

    async def set_current_project(
        self,
        session_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> WorkspaceSession:
        if session_id not in self._sessions:
            raise UnknownSessionError(session_id)
        session = self._sessions[session_id]
        if session.current_project_id == project_id:
            return session.model_copy(deep=True)
        updated = session.model_copy(
            update={"current_project_id": project_id}
        )
        self._sessions[session_id] = updated
        self._append_activity(
            session_id,
            ActivityKind.PROJECT_CHANGED,
            subject=str(project_id),
        )
        await self._publish(
            evt.SESSION_CURRENT_PROJECT_CHANGED,
            {
                "session_id": str(session_id),
                "project_id": str(project_id),
                "previous_project_id": (
                    str(session.current_project_id)
                    if session.current_project_id is not None
                    else None
                ),
            },
        )
        return updated.model_copy(deep=True)

    # ------------------------------------------------------------------ #
    # Recent activity ring
    # ------------------------------------------------------------------ #
    def recent_activity(
        self,
        session_id: uuid.UUID,
        *,
        limit: int = 20,
    ) -> list[RecentActivity]:
        if session_id not in self._sessions:
            raise UnknownSessionError(session_id)
        if limit < 0:
            raise ValueError("limit must be >= 0")
        activities = self._sessions[session_id].recent_activity
        # Return the most-recent N (the ring stores oldest-first).
        if limit == 0:
            return []
        return list(activities[-limit:])

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    async def persist(self, session_id: uuid.UUID) -> WorkspaceSession:
        record = self._sessions.get(session_id)
        if record is None:
            raise UnknownSessionError(session_id)
        await self._store.save(record.model_copy(deep=True))
        return record.model_copy(deep=True)

    async def restore(
        self, session_id: uuid.UUID
    ) -> WorkspaceSession | None:
        loaded = await self._store.load(session_id)
        if loaded is None:
            return None
        self._sessions[session_id] = loaded.model_copy(deep=True)
        await self._publish(
            evt.SESSION_RESTORED,
            {"session_id": str(session_id), "user_id": loaded.user_id},
        )
        return loaded.model_copy(deep=True)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _append_activity(
        self,
        session_id: uuid.UUID,
        kind: ActivityKind,
        *,
        subject: str,
    ) -> None:
        session = self._sessions[session_id]
        ring = session.recent_activity
        ring.append(
            RecentActivity(kind=kind, subject=subject, timestamp=self._clock())
        )
        # Trim oldest-first when over capacity.
        if len(ring) > self._capacity:
            del ring[: len(ring) - self._capacity]
        # Reassign to trigger model update.
        self._sessions[session_id] = session.model_copy(
            update={"recent_activity": list(ring)}
        )

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module=SOURCE_MODULE,
                correlation_id=uuid.uuid4(),
                payload=payload,
            )
        )


__all__ = ["SessionManager", "InMemorySessionStore"]