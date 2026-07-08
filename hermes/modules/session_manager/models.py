"""Pydantic data contracts for the Session Manager."""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ActivityKind(str, Enum):
    """The discrete activity kinds the Session Manager records in
    the recent-activity ring. Kept narrow on purpose: the manager
    is metadata only."""

    SESSION_STARTED = "session_started"
    SESSION_ENDED = "session_ended"
    WORKSPACE_CHANGED = "workspace_changed"
    APPLICATION_CHANGED = "application_changed"
    MISSION_CHANGED = "mission_changed"
    PROJECT_CHANGED = "project_changed"
    USER_CHANGED = "user_changed"


class RecentActivity(BaseModel):
    """One entry in the per-session recent activity ring."""

    kind: ActivityKind
    subject: str
    timestamp: datetime


class WorkspaceSession(BaseModel):
    """One session. Identity, owner, current-{workspace, application,
    mission, project, user} pointers, and a bounded recent-activity
    ring. The session id is also the Session Manager's dict key."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: str
    current_workspace_id: uuid.UUID | None = None
    current_application_id: str | None = None
    current_mission_id: uuid.UUID | None = None
    current_project_id: uuid.UUID | None = None
    recent_activity: list[RecentActivity] = Field(default_factory=list)
    started_at: datetime
    ended_at: datetime | None = None


__all__ = ["ActivityKind", "RecentActivity", "WorkspaceSession"]
