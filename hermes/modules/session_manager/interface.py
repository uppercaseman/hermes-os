"""Public entry point for the Session Manager.

`build_session_manager(...)` mirrors every other module's
interface.py convention. The `workspace_manager` argument is a
`WorkspaceAccessor` -- the Session Manager only needs to resolve
a `workspace_id` to confirm it exists, and the Protocol keeps the
Session Manager free of any concrete dependency.
"""
from __future__ import annotations

from typing import Callable, Optional

from hermes.core.event_bus.interface import EventBus
from hermes.modules.session_manager.contracts import (
    SessionStore,
    WorkspaceAccessor,
)
from hermes.modules.session_manager.service import (
    InMemorySessionStore,
    SessionManager,
)

__all__ = ["SessionManager", "build_session_manager"]


def build_session_manager(
    *,
    event_bus: EventBus | None = None,
    workspace_manager: WorkspaceAccessor | None = None,
    recent_activity_capacity: int = 50,
    clock: Optional[Callable] = None,
    store: SessionStore | None = None,
) -> SessionManager:
    """Constructs a SessionManager.

    `recent_activity_capacity` bounds the per-session recent-activity
    ring (oldest-first eviction once full). `clock` is a callable
    returning `datetime`; default uses `datetime.now(timezone.utc)` --
    tests pass a fake clock to control timestamps. `store` is the
    `SessionStore` Protocol; default is an in-process
    `InMemorySessionStore`.
    """
    return SessionManager(
        event_bus=event_bus,
        workspace_manager=workspace_manager,
        recent_activity_capacity=recent_activity_capacity,
        clock=clock,
        store=store or InMemorySessionStore(),
    )
