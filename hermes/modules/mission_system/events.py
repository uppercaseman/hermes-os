"""Event-type constants the Mission System publishes.

Namespaced `mission_system.*`. All publishing is a no-op if the system
was constructed without an event bus -- see service.py.
"""

MISSION_CREATED = "mission_system.mission.created"
TEAM_ASSIGNED = "mission_system.team.assigned"
APPROVAL_DECIDED = "mission_system.approval.decided"
MISSION_AWAITING_APPROVAL = "mission_system.mission.awaiting_approval"
MISSION_STARTED = "mission_system.mission.started"
MISSION_COMPLETED = "mission_system.mission.completed"
MISSION_FAILED = "mission_system.mission.failed"
MISSION_DISSOLVED = "mission_system.mission.dissolved"
