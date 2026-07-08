"""Public entry point for the Workspace Manager.

`build_workspace_manager(...)` mirrors every other module's
interface.py convention. When `store` is omitted, an in-memory
`InMemoryWorkspaceStore` is constructed -- useful for tests and
ephemeral runs.
"""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.modules.application_registry.contracts import ApplicationSource
from hermes.modules.workspace_manager.contracts import WorkspaceStore
from hermes.modules.workspace_manager.service import (
    InMemoryWorkspaceStore,
    WorkspaceManager,
)

__all__ = ["WorkspaceManager", "build_workspace_manager"]


def build_workspace_manager(
    *,
    event_bus: EventBus | None = None,
    application_registry: ApplicationSource | None = None,
    store: WorkspaceStore | None = None,
) -> WorkspaceManager:
    """Constructs a WorkspaceManager.

    - `event_bus`: optional. When absent, every event publish
      silently no-ops.
    - `application_registry`: optional. When present, the Manager
      uses it to validate `set_current_application`. When absent,
      that method accepts any string id (useful for tests).
    - `store`: optional. When absent, an `InMemoryWorkspaceStore`
      is used. Call `await save_workspace(workspace_id)` to persist.
    """
    return WorkspaceManager(
        event_bus=event_bus,
        application_registry=application_registry,
        store=store or InMemoryWorkspaceStore(),
    )
