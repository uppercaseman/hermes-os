"""Mission System-specific exception types."""
from __future__ import annotations

import uuid


class UnknownMissionError(Exception):
    def __init__(self, mission_id: uuid.UUID) -> None:
        self.mission_id = mission_id
        super().__init__(f"no mission with id {mission_id}")


class MissionNotReadyError(Exception):
    """Raised by `execute_mission` when called before a team has ever
    been assigned -- a precondition check, before any execution is
    attempted, so it raises rather than becoming a failed mission."""

    def __init__(self, mission_id: uuid.UUID, status: str) -> None:
        self.mission_id = mission_id
        self.status = status
        super().__init__(f"mission {mission_id} is not ready to execute (status={status!r})")


class UnknownApprovalGateError(Exception):
    def __init__(self, mission_id: uuid.UUID, gate_name: str) -> None:
        self.mission_id = mission_id
        self.gate_name = gate_name
        super().__init__(f"mission {mission_id} has no required approval gate named {gate_name!r}")


class UnknownRoleTemplateError(Exception):
    def __init__(self, role_name: str) -> None:
        self.role_name = role_name
        super().__init__(f"no role template registered for {role_name!r}")


class MissionSystemConfigError(Exception):
    """Raised for a missing required collaborator. Distinguish: a missing
    Commander is checked BEFORE any execution attempt (raises, like a
    precondition); a missing IntentRouter needed to infer a workflow is
    discovered DURING the execution attempt and is caught, failing the
    mission instead -- see service.py's `execute_mission`."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
