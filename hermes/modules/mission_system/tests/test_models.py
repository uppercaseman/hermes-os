import uuid

import pytest
from pydantic import ValidationError

from hermes.modules.mission_system.models import Mission, SpecialistRole, SuccessCriterion


def test_mission_defaults_to_draft_with_no_team():
    mission = Mission(goal="research quantum computing")

    assert mission.status == "draft"
    assert mission.assigned_team == []
    assert mission.outputs == {}


# --------------------------------------------------------------------- #
# ADR 0017 -- Mission Lifecycle Reconciliation
# The MissionStatus Literal now accepts all 13 canonical states from
# ADR 0014, plus the three implementation-nicknamed values the runtime
# writes today (`draft`, `team_assigned`, `active`).
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "canonical_state",
    [
        "created",
        "planned",
        "awaiting_approval",
        "ready",
        "running",
        "paused",
        "waiting",
        "blocked",
        "completed",
        "failed",
        "cancelled",
        "dissolved",
        "archived",
    ],
)
def test_mission_accepts_every_canonical_lifecycle_state(canonical_state):
    """ADR 0017: Mission.status must accept any of the 13 canonical
    states from ADR 0014 / Mission Lifecycle spec, not just the seven
    the runtime writes today."""
    mission = Mission(goal="x", status=canonical_state)
    assert mission.status == canonical_state


@pytest.mark.parametrize(
    "legacy_state",
    ["draft", "team_assigned", "active"],
)
def test_mission_continues_to_accept_legacy_runtime_state_names(legacy_state):
    """ADR 0017: the seven status names runtime code writes today must
    remain valid -- this is the backward-compatibility guarantee that
    makes the Literal expansion a strictly additive change."""
    mission = Mission(goal="x", status=legacy_state)
    assert mission.status == legacy_state


def test_mission_rejects_an_unknown_lifecycle_state():
    """ADR 0017: the Literal must remain a closed set -- a string that is
    none of the canonical 13 plus the three legacy names must be rejected
    at construction time (the assignment-time validation policy is left
    to a future decision; construction-time is the contract here)."""
    with pytest.raises(ValidationError):
        Mission(goal="x", status="frobnicated")  # type: ignore[arg-type]


def test_success_criterion_defaults_to_unjudged():
    criterion = SuccessCriterion(description="produces a working demo")

    assert criterion.met is None


def test_specialist_role_permission_helpers():
    role = SpecialistRole(
        role_name="Developer",
        mission_id=uuid.uuid4(),
        agent_id="mission:x:Developer",
        required_capabilities=["code_generation"],
        allowed_tools=["mock_research"],
        memory_scopes=["workflow"],
    )

    assert role.can_use_capability("code_generation") is True
    assert role.can_use_capability("vision") is False
    assert role.can_use_tool("mock_research") is True
    assert role.can_use_tool("openai") is False
    assert role.can_access_memory_scope("workflow") is True
    assert role.can_access_memory_scope("persistent") is False
