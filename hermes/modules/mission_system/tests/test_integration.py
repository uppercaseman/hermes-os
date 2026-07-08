"""Integration test: real Commander, real Workflow Engine (via the real
`WorkflowEngineTaskDispatcher` bridge), real Intent Router, real Memory
Manager, real Event Bus, and a real MissionSystem/TeamBuilder -- wired
together and run for real. Proves the required integration list
(Commander, Workflow Engine, Intent Router, Event Bus, Memory Manager)
actually works end to end, not just that each module works alone.
"""
from __future__ import annotations

from hermes.core.commander.interface import build_commander
from hermes.core.commander.models import ApprovalDecision, MemoryRequirement
from hermes.core.commander.tests.fakes import (
    FakeAgentResolver,
    FakeApprovalPolicy,
    FakeMemoryResolver,
    FakeToolResolver,
)
from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.intent_router.interface import WorkflowRoute, build_intent_router
from hermes.modules.memory_manager.errors import MemoryPermissionDeniedError
from hermes.modules.memory_manager.interface import build_memory_manager
from hermes.modules.mission_system.interface import build_mission_system, build_team_builder
from hermes.modules.workflow_engine.commander_bridge import WorkflowEngineTaskDispatcher
from hermes.modules.workflow_engine.interface import build_workflow_engine
from hermes.modules.workflow_engine.templates import sequential_template

import pytest


async def test_full_mission_lifecycle_across_real_modules():
    bus = InMemoryEventBus()
    memory_manager = build_memory_manager(event_bus=bus)

    engine = build_workflow_engine(event_bus=bus, memory_manager=memory_manager)
    engine.register_workflow(sequential_template("build_feature", "build_feature", ["design", "implement", "test"]))

    router = build_intent_router()
    router.add_route(
        WorkflowRoute(workflow_id="build_feature", intent_names=["build_feature"], keywords=["build", "implement"])
    )

    dispatcher = WorkflowEngineTaskDispatcher(engine=engine, event_bus=bus)

    commander = build_commander(
        event_bus=bus,
        intent_classifier=router,
        workflow_resolver=router,  # the SAME IntentRouter instance satisfies both Commander protocols
        agent_resolver=FakeAgentResolver([]),
        tool_resolver=FakeToolResolver([]),
        memory_resolver=FakeMemoryResolver(MemoryRequirement(scope="session", keys=[])),
        approval_policy=FakeApprovalPolicy(ApprovalDecision(required=False)),
        task_dispatcher=dispatcher,
    )

    team_builder = build_team_builder(memory_manager=memory_manager)
    mission_system = build_mission_system(
        commander=commander, intent_router=router, event_bus=bus, team_builder=team_builder
    )

    events_seen: list[str] = []

    async def capture(event):
        events_seen.append(event.event_type)

    await bus.subscribe("*", capture)

    # Create a mission with no required_workflows -- the Mission System
    # must infer "build_feature" via the real Intent Router from the goal
    # text alone.
    mission = await mission_system.create_mission(
        goal="please build and implement the new feature",
        success_criteria=["feature works end to end"],
        required_capabilities=["code_generation"],
    )
    assert mission.status == "draft"

    await mission_system.assign_team(mission.id)
    assert [r.role_name for r in mission.assigned_team] == ["Developer"]
    developer_agent_id = mission.assigned_team[0].agent_id

    # The Developer role genuinely can read/write the mission's shared
    # memory pool -- this is Memory Manager's real permission system,
    # not a mock.
    await memory_manager.save(
        requesting_agent_id=developer_agent_id,
        scope="workflow",
        owner_agent_id=str(mission.id),
        key="notes",
        value={"progress": "started"},
    )
    entry = await memory_manager.get_by_key(
        requesting_agent_id=developer_agent_id, scope="workflow", owner_agent_id=str(mission.id), key="notes"
    )
    assert entry is not None and entry.value == {"progress": "started"}

    result = await mission_system.execute_mission(mission.id)

    assert result.status == "completed"
    assert result.required_workflows == ["build_feature"]  # inferred, not given explicitly
    assert "build_feature" in result.outputs

    mission_system.mark_success_criterion(mission.id, "feature works end to end", met=True)
    assert mission_system.get_mission(mission.id).success_criteria[0].met is True

    dissolved = await mission_system.dissolve_mission(mission.id)
    assert dissolved.status == "dissolved"

    # After dissolution, the SAME agent_id that could read/write the
    # shared pool a moment ago is denied -- the grant was genuinely
    # revoked, not just marked dissolved on the SpecialistRole object.
    with pytest.raises(MemoryPermissionDeniedError):
        await memory_manager.get_by_key(
            requesting_agent_id=developer_agent_id, scope="workflow", owner_agent_id=str(mission.id), key="notes"
        )

    # Every required integration published at least one real event.
    assert "mission_system.mission.created" in events_seen
    assert "mission_system.team.assigned" in events_seen
    assert "mission_system.mission.completed" in events_seen
    assert "mission_system.mission.dissolved" in events_seen
    assert "commander.request.received" in events_seen
    assert "workflow_engine.run.started" in events_seen
    assert "workflow_engine.run.completed" in events_seen
    assert "memory_manager.entry.saved" in events_seen
