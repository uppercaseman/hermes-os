import inspect
import uuid

import pytest

from hermes.modules.mission_system.errors import (
    MissionNotReadyError,
    MissionSystemConfigError,
    UnknownApprovalGateError,
    UnknownMissionError,
)
from hermes.modules.mission_system.events import (
    MISSION_AWAITING_APPROVAL,
    MISSION_COMPLETED,
    MISSION_CREATED,
    MISSION_DISSOLVED,
    MISSION_FAILED,
    TEAM_ASSIGNED,
)
from hermes.modules.mission_system.interface import build_mission_system, build_team_builder
from hermes.modules.mission_system.tests.fakes import FakeCommander, FakeIntentRouter


# --------------------------------------------------------------------- #
# Creation / team assignment
# --------------------------------------------------------------------- #

async def test_create_mission_starts_in_draft(mission_system):
    mission = await mission_system.create_mission(goal="ship the feature")

    assert mission.status == "draft"
    assert mission_system.get_mission(mission.id).id == mission.id


async def test_assign_team_builds_team_and_updates_status(mission_system):
    mission = await mission_system.create_mission(goal="write some code", required_capabilities=["code_generation"])

    updated = await mission_system.assign_team(mission.id)

    assert updated.status == "team_assigned"
    assert [r.role_name for r in updated.assigned_team] == ["Developer"]


async def test_unknown_mission_id_raises_for_every_lookup(mission_system):
    bogus = uuid.uuid4()
    with pytest.raises(UnknownMissionError):
        mission_system.get_mission(bogus)
    with pytest.raises(UnknownMissionError):
        await mission_system.assign_team(bogus)
    with pytest.raises(UnknownMissionError):
        await mission_system.execute_mission(bogus)


# --------------------------------------------------------------------- #
# Execution preconditions
# --------------------------------------------------------------------- #

async def test_execute_before_team_assigned_raises(mission_system):
    mission = await mission_system.create_mission(goal="x")

    with pytest.raises(MissionNotReadyError):
        await mission_system.execute_mission(mission.id)


async def test_execute_without_a_configured_commander_raises():
    system = build_mission_system()  # no commander at all
    mission = await system.create_mission(goal="x", required_workflows=["wf1"])
    await system.assign_team(mission.id)

    with pytest.raises(MissionSystemConfigError):
        await system.execute_mission(mission.id)


# --------------------------------------------------------------------- #
# Approval gate
# --------------------------------------------------------------------- #

async def test_execute_with_pending_approval_does_not_call_commander(mission_system, fake_commander):
    mission = await mission_system.create_mission(goal="x", required_workflows=["wf1"], required_approvals=["legal"])
    await mission_system.assign_team(mission.id)

    result = await mission_system.execute_mission(mission.id)

    assert result.status == "awaiting_approval"
    assert fake_commander.requests == []


async def test_approving_an_unknown_gate_raises(mission_system):
    mission = await mission_system.create_mission(goal="x", required_approvals=["legal"])

    with pytest.raises(UnknownApprovalGateError):
        await mission_system.approve(mission.id, "not-a-real-gate", approved=True, approver="ops")


async def test_approving_all_gates_allows_execution_to_proceed(mission_system, fake_commander):
    mission = await mission_system.create_mission(goal="x", required_workflows=["wf1"], required_approvals=["legal"])
    await mission_system.assign_team(mission.id)

    await mission_system.approve(mission.id, "legal", approved=True, approver="ops-lead")
    result = await mission_system.execute_mission(mission.id)

    assert result.status == "completed"
    assert len(fake_commander.requests) == 1


# --------------------------------------------------------------------- #
# Execution against required_workflows
# --------------------------------------------------------------------- #

async def test_execute_dispatches_one_request_per_required_workflow(mission_system, fake_commander):
    mission = await mission_system.create_mission(goal="do research then write code", required_workflows=["research", "code"])
    await mission_system.assign_team(mission.id)

    result = await mission_system.execute_mission(mission.id)

    assert result.status == "completed"
    assert len(fake_commander.requests) == 2
    assert set(result.outputs.keys()) == {"research", "code"}


async def test_execute_stops_at_the_first_failing_workflow():
    commander = FakeCommander(outcomes=["failed", "completed"])
    system = build_mission_system(commander=commander, team_builder=build_team_builder())
    mission = await system.create_mission(goal="x", required_workflows=["wf1", "wf2"])
    await system.assign_team(mission.id)

    result = await system.execute_mission(mission.id)

    assert result.status == "failed"
    assert len(commander.requests) == 1  # never attempted wf2


async def test_execute_infers_a_workflow_via_intent_router_when_none_given():
    commander = FakeCommander()
    router = FakeIntentRouter(workflow_id="inferred_workflow")
    system = build_mission_system(commander=commander, intent_router=router, team_builder=build_team_builder())
    mission = await system.create_mission(goal="please research the moon")
    await system.assign_team(mission.id)

    result = await system.execute_mission(mission.id)

    assert result.status == "completed"
    assert list(result.outputs.keys()) == ["inferred_workflow"]


async def test_execute_fails_cleanly_when_no_workflows_and_no_router_to_infer():
    commander = FakeCommander()
    system = build_mission_system(commander=commander, team_builder=build_team_builder())  # no intent_router
    mission = await system.create_mission(goal="x")  # no required_workflows either
    await system.assign_team(mission.id)

    result = await system.execute_mission(mission.id)

    assert result.status == "failed"  # caught, not raised -- see MissionSystemConfigError docstring
    assert commander.requests == []


async def test_execute_fails_cleanly_when_the_goal_is_unroutable():
    commander = FakeCommander()
    router = FakeIntentRouter(raise_unknown=True)
    system = build_mission_system(commander=commander, intent_router=router, team_builder=build_team_builder())
    mission = await system.create_mission(goal="what's for lunch")
    await system.assign_team(mission.id)

    result = await system.execute_mission(mission.id)

    assert result.status == "failed"


# --------------------------------------------------------------------- #
# Dissolution
# --------------------------------------------------------------------- #

async def test_dissolve_mission_after_completion(mission_system):
    mission = await mission_system.create_mission(goal="x", required_workflows=["wf1"])
    await mission_system.assign_team(mission.id)
    await mission_system.execute_mission(mission.id)

    dissolved = await mission_system.dissolve_mission(mission.id)

    assert dissolved.status == "dissolved"
    assert all(role.status == "dissolved" for role in dissolved.assigned_team)


async def test_dissolve_mission_from_draft_is_allowed(mission_system):
    mission = await mission_system.create_mission(goal="abandoned early")

    dissolved = await mission_system.dissolve_mission(mission.id)

    assert dissolved.status == "dissolved"


# --------------------------------------------------------------------- #
# Success criteria bookkeeping
# --------------------------------------------------------------------- #

async def test_mark_success_criterion_updates_the_matching_entry(mission_system):
    mission = await mission_system.create_mission(goal="x", success_criteria=["demo runs end to end"])

    updated = mission_system.mark_success_criterion(mission.id, "demo runs end to end", met=True)

    assert updated.success_criteria[0].met is True


def test_mark_success_criterion_on_unknown_mission_raises(mission_system):
    with pytest.raises(UnknownMissionError):
        mission_system.mark_success_criterion(uuid.uuid4(), "anything", met=True)


async def test_mark_unknown_criterion_on_a_known_mission_raises_value_error(mission_system):
    mission = await mission_system.create_mission(goal="x", success_criteria=["a real criterion"])

    with pytest.raises(ValueError):
        mission_system.mark_success_criterion(mission.id, "a criterion that was never declared", met=True)


# --------------------------------------------------------------------- #
# Query methods are synchronous by design
# --------------------------------------------------------------------- #

def test_query_methods_are_synchronous(mission_system):
    assert not inspect.iscoroutinefunction(mission_system.get_mission)
    assert not inspect.iscoroutinefunction(mission_system.get_mission_status)
    assert not inspect.iscoroutinefunction(mission_system.list_missions)


async def test_list_missions_returns_every_created_mission(mission_system):
    a = await mission_system.create_mission(goal="a")
    b = await mission_system.create_mission(goal="b")

    ids = {m.id for m in mission_system.list_missions()}

    assert ids == {a.id, b.id}


# --------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------- #

async def test_lifecycle_events_are_published(bus):
    commander = FakeCommander()
    system = build_mission_system(commander=commander, event_bus=bus, team_builder=build_team_builder())
    seen = []

    async def capture(event):
        seen.append(event.event_type)

    await bus.subscribe("*", capture)

    mission = await system.create_mission(goal="x", required_workflows=["wf1"])
    await system.assign_team(mission.id)
    await system.execute_mission(mission.id)
    await system.dissolve_mission(mission.id)

    assert MISSION_CREATED in seen
    assert TEAM_ASSIGNED in seen
    assert MISSION_COMPLETED in seen
    assert MISSION_DISSOLVED in seen


async def test_awaiting_approval_and_failed_events_are_published(bus):
    commander = FakeCommander(outcomes=["failed"])
    system = build_mission_system(commander=commander, event_bus=bus, team_builder=build_team_builder())
    seen = []

    async def capture(event):
        seen.append(event.event_type)

    await bus.subscribe("*", capture)

    approval_mission = await system.create_mission(goal="x", required_workflows=["wf1"], required_approvals=["legal"])
    await system.assign_team(approval_mission.id)
    await system.execute_mission(approval_mission.id)
    assert MISSION_AWAITING_APPROVAL in seen

    failing_mission = await system.create_mission(goal="x", required_workflows=["wf1"])
    await system.assign_team(failing_mission.id)
    await system.execute_mission(failing_mission.id)
    assert MISSION_FAILED in seen
