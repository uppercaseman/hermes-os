"""Session Manager -- one WorkspaceSession per active Hermes login.

The Session Manager owns the running session concept: a single
`WorkspaceSession` carries the current user id, current workspace
id, current application id, current mission id, current project
id, and a bounded recent-activity ring. It depends on
`WorkspaceManager` only via the `WorkspaceAccessor` Protocol, so
the Session Manager never imports the Workspace Manager's
concrete class.
"""
from hermes.modules.session_manager.interface import build_session_manager
from hermes.modules.session_manager.service import SessionManager

__all__ = ["SessionManager", "build_session_manager"]
