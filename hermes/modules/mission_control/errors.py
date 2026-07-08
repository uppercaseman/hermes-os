"""Mission Control-specific exception types."""
from __future__ import annotations

import uuid


class MissionControlError(Exception):
    """Base for mission-control-level errors."""


class UnknownMissionError(MissionControlError):
    def __init__(self, mission_id: uuid.UUID) -> None:
        self.mission_id = mission_id
        super().__init__(f"mission {mission_id!s} is not known to the source")


class MissionControlConfigError(MissionControlError):
    """Construction-time configuration error."""


__all__ = [
    "MissionControlError",
    "UnknownMissionError",
    "MissionControlConfigError",
]