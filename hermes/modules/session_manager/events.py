"""Session Manager event vocabulary.

Namespaced `session_manager.*`. Eight events cover the full session
lifecycle: start / end / restore, plus each of the five
current-pointer mutations.
"""

SESSION_STARTED = "session_manager.session.started"
SESSION_ENDED = "session_manager.session.ended"
SESSION_RESTORED = "session_manager.session.restored"
SESSION_CURRENT_WORKSPACE_CHANGED = "session_manager.session.current_workspace_changed"
SESSION_CURRENT_APPLICATION_CHANGED = "session_manager.session.current_application_changed"
SESSION_CURRENT_MISSION_CHANGED = "session_manager.session.current_mission_changed"
SESSION_CURRENT_PROJECT_CHANGED = "session_manager.session.current_project_changed"
SESSION_CURRENT_USER_CHANGED = "session_manager.session.current_user_changed"

__all__ = [
    "SESSION_STARTED",
    "SESSION_ENDED",
    "SESSION_RESTORED",
    "SESSION_CURRENT_WORKSPACE_CHANGED",
    "SESSION_CURRENT_APPLICATION_CHANGED",
    "SESSION_CURRENT_MISSION_CHANGED",
    "SESSION_CURRENT_PROJECT_CHANGED",
    "SESSION_CURRENT_USER_CHANGED",
]