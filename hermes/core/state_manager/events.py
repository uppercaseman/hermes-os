"""Event-type constants the State Manager publishes.

Namespaced `state_manager.*`. All publishing is a no-op if the manager
was constructed without an event bus -- see service.py.
"""

STATE_REPORTED = "state_manager.module.state_reported"
RESTART_REQUESTED = "state_manager.module.restart_requested"
RESTART_FAILED = "state_manager.module.restart_failed"
RECOVERY_EXHAUSTED = "state_manager.module.recovery_exhausted"
