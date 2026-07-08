"""Workspace Manager event vocabulary.

Namespaced `workspace_manager.*`. Eight events cover the full
manager lifecycle: create / open / close / focus / save, plus
mission open / close and layout change.
"""

WORKSPACE_CREATED = "workspace_manager.workspace.created"
WORKSPACE_OPENED = "workspace_manager.workspace.opened"
WORKSPACE_CLOSED = "workspace_manager.workspace.closed"
WORKSPACE_FOCUSED = "workspace_manager.workspace.focused"
WORKSPACE_SAVED = "workspace_manager.workspace.saved"
WORKSPACE_MISSION_OPENED = "workspace_manager.workspace.mission_opened"
WORKSPACE_MISSION_CLOSED = "workspace_manager.workspace.mission_closed"
LAYOUT_CHANGED = "workspace_manager.layout.changed"

__all__ = [
    "WORKSPACE_CREATED",
    "WORKSPACE_OPENED",
    "WORKSPACE_CLOSED",
    "WORKSPACE_FOCUSED",
    "WORKSPACE_SAVED",
    "WORKSPACE_MISSION_OPENED",
    "WORKSPACE_MISSION_CLOSED",
    "LAYOUT_CHANGED",
]
