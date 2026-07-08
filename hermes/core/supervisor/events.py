"""Event-type constants the Supervisor publishes.

Namespaced `supervisor.*`, following the OS-wide `domain.entity.action`
convention used by Commander's events.py.
"""

UNIT_STARTING = "supervisor.unit.starting"
UNIT_STARTED = "supervisor.unit.started"
UNIT_CRASHED = "supervisor.unit.crashed"
UNIT_UNHEALTHY = "supervisor.unit.unhealthy"
UNIT_RESTARTING = "supervisor.unit.restarting"
UNIT_RESTART_SKIPPED = "supervisor.unit.restart_skipped"
UNIT_RESTART_EXHAUSTED = "supervisor.unit.restart_exhausted"
UNIT_STOPPED = "supervisor.unit.stopped"
