"""Workspace Manager -- the active workspace, current application, layout, docking, window state, open missions, open projects, and workspace restoration.

The Workspace Manager is the heart of the workspace layer: it owns
the in-memory representation of one or more `Workspace` records,
knows which one is currently active, persists workspaces on
demand via a pluggable `WorkspaceStore` Protocol, and validates
`set_current_application` calls against the
`ApplicationRegistry` Protocol.

The Manager has no knowledge of Commander, Mission System,
Workflow Engine, or any core Hermes module. Its only downward
dependency is `ApplicationRegistry` (validated via the
`ApplicationSource` Protocol) and the optional EventBus.
"""
from hermes.modules.workspace_manager.interface import build_workspace_manager
from hermes.modules.workspace_manager.service import WorkspaceManager

__all__ = ["WorkspaceManager", "build_workspace_manager"]
