import pytest

from hermes.modules.mission_system.errors import UnknownRoleTemplateError
from hermes.modules.mission_system.interface import build_team_builder
from hermes.modules.mission_system.models import Mission
from hermes.modules.mission_system.roles import RoleTemplate


def _mission(**kwargs) -> Mission:
    return Mission(goal="do the thing", **kwargs)


def test_explicit_requested_roles_bypass_capability_inference(team_builder):
    mission = _mission(required_capabilities=["code_generation"], requested_roles=["QA"])

    roles = team_builder.determine_required_roles(mission)

    assert roles == ["QA"]  # not "Developer", even though code_generation would infer it


def test_capability_based_inference_matches_developer():
    from hermes.modules.mission_system.interface import build_team_builder

    builder = build_team_builder()
    mission = _mission(required_capabilities=["code_generation"])

    roles = builder.determine_required_roles(mission)

    assert roles == ["Developer"]


def test_capability_based_inference_matches_research_specialist():
    from hermes.modules.mission_system.interface import build_team_builder

    builder = build_team_builder()
    mission = _mission(required_capabilities=["browser_automation"])

    roles = builder.determine_required_roles(mission)

    assert roles == ["Research Specialist"]


def test_no_matching_capability_yields_no_roles():
    from hermes.modules.mission_system.interface import build_team_builder

    builder = build_team_builder()
    mission = _mission(required_capabilities=["vision"])

    roles = builder.determine_required_roles(mission)

    assert roles == []


def test_build_team_creates_roles_with_mission_scoped_agent_ids(team_builder):
    mission = _mission(required_capabilities=["code_generation"])

    team = team_builder.build_team(mission)

    assert len(team) == 1
    role = team[0]
    assert role.role_name == "Developer"
    assert role.agent_id == f"mission:{mission.id}:Developer"
    assert "code_generation" in role.required_capabilities
    assert "workflow" in role.memory_scopes


def test_build_team_grants_shared_mission_memory_access(team_builder, fake_memory):
    mission = _mission(required_capabilities=["code_generation"])

    team = team_builder.build_team(mission)

    assert len(fake_memory.grants) == 1
    agent_id, owner_agent_id, can_read, can_write = fake_memory.grants[0]
    assert agent_id == team[0].agent_id
    assert owner_agent_id == str(mission.id)
    assert can_read is True and can_write is True


def test_build_team_works_without_a_memory_manager_configured():
    from hermes.modules.mission_system.interface import build_team_builder

    builder = build_team_builder()  # no memory_manager
    mission = _mission(required_capabilities=["code_generation"])

    team = builder.build_team(mission)  # must not raise

    assert len(team) == 1


def test_build_team_raises_for_an_unknown_explicit_role(team_builder):
    mission = _mission(requested_roles=["Time Traveler"])

    with pytest.raises(UnknownRoleTemplateError):
        team_builder.build_team(mission)


def test_dissolve_team_marks_roles_dissolved_and_revokes_shared_access(team_builder, fake_memory):
    mission = _mission(required_capabilities=["code_generation"])
    mission.assigned_team = team_builder.build_team(mission)

    team_builder.dissolve_team(mission)

    assert all(role.status == "dissolved" for role in mission.assigned_team)
    assert fake_memory.revocations == [(mission.assigned_team[0].agent_id, str(mission.id))]


def test_register_template_adds_a_new_role():
    from hermes.modules.mission_system.interface import build_team_builder

    builder = build_team_builder()
    builder.register_template(
        RoleTemplate(role_name="Data Analyst", trigger_capabilities=["reasoning"], default_capabilities=["reasoning"])
    )
    mission = _mission(required_capabilities=["reasoning"])

    roles = builder.determine_required_roles(mission)

    assert roles == ["Data Analyst"]
