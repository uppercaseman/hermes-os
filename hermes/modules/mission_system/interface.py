"""Public entry point for the Mission System.

Everything outside this package imports from here, never from
service.py/team_builder.py directly -- mirrors every other module's
interface.py convention.
"""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.modules.mission_system.contracts import IntentResolver, MemoryPermissionGranter, RequestHandler
from hermes.modules.mission_system.errors import (
    MissionNotReadyError,
    MissionSystemConfigError,
    UnknownApprovalGateError,
    UnknownMissionError,
    UnknownRoleTemplateError,
)
from hermes.modules.mission_system.models import (
    ApprovalRecord,
    Mission,
    MissionStatus,
    SpecialistRole,
    SuccessCriterion,
)
from hermes.modules.mission_system.roles import DEFAULT_ROLE_TEMPLATES, RoleTemplate
from hermes.modules.mission_system.service import MissionSystem
from hermes.modules.mission_system.team_builder import TeamBuilder

__all__ = [
    "MissionSystem",
    "TeamBuilder",
    "Mission",
    "MissionStatus",
    "SuccessCriterion",
    "ApprovalRecord",
    "SpecialistRole",
    "RoleTemplate",
    "DEFAULT_ROLE_TEMPLATES",
    "RequestHandler",
    "IntentResolver",
    "MemoryPermissionGranter",
    "UnknownMissionError",
    "MissionNotReadyError",
    "UnknownApprovalGateError",
    "UnknownRoleTemplateError",
    "MissionSystemConfigError",
    "build_mission_system",
    "build_team_builder",
]


def build_team_builder(
    *, memory_manager: MemoryPermissionGranter | None = None, templates: list[RoleTemplate] | None = None
) -> TeamBuilder:
    return TeamBuilder(memory_manager=memory_manager, templates=templates)


def build_mission_system(
    *,
    commander: RequestHandler | None = None,
    intent_router: IntentResolver | None = None,
    event_bus: EventBus | None = None,
    team_builder: TeamBuilder | None = None,
) -> MissionSystem:
    return MissionSystem(commander=commander, intent_router=intent_router, event_bus=event_bus, team_builder=team_builder)
