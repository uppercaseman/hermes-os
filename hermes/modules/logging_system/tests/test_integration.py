"""Integration test: real Commander, Mission System, Workflow Engine,
Task Queue + Worker, Tool Manager (with a scripted adapter), State
Manager, and one real Event Bus -- with a real LoggingSystem capturing
everything and answering every required query dimension against what
actually happened, not a synthetic fixture.
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
from hermes.core.state_manager.interface import build_state_manager
from hermes.core.supervisor.policy import RetryPolicy
from hermes.modules.intent_router.interface import WorkflowRoute, build_intent_router
from hermes.modules.logging_system.interface import build_logging_system
from hermes.modules.memory_manager.interface import build_memory_manager
from hermes.modules.mission_system.interface import build_mission_system, build_team_builder
from hermes.modules.task_queue.commander_dispatcher import TaskQueueDispatcher
from hermes.modules.task_queue.interface import build_task_queue, build_worker
from hermes.modules.task_queue.workflow_executor import WorkflowEngineTaskExecutor
from hermes.modules.tool_manager.interface import build_tool_manager
from hermes.modules.tool_manager.models import ToolAdapterConfig
from hermes.modules.tool_manager.tests.fakes import ScriptedToolAdapter
from hermes.modules.workflow_engine.interface import build_workflow_engine
from hermes.modules.workflow_engine.models import StepDefinition, WorkflowDefinition


async def test_logging_system_captures_and_answers_every_query_across_a_real_mission_run():
    bus = InMemoryEventBus()
    logging_system = build_logging_system(event_bus=bus)
    await logging_system.start()

    state_manager = build_state_manager(event_bus=bus)
    memory_manager = build_memory_manager(event_bus=bus)

    tool_manager = build_tool_manager(event_bus=bus)
    tool_manager.register_adapter(ScriptedToolAdapter(name="mock_research"), ToolAdapterConfig(name="mock_research"))

    engine = build_workflow_engine(event_bus=bus, memory_manager=memory_manager, tool_manager=tool_manager)
    engine.register_workflow(
        WorkflowDefinition(
            workflow_id="build_feature",
            name="build_feature",
            steps=[StepDefinition(name="do_research", kind="tool_call", tool_name="mock_research", operation="research")],
        )
    )

    task_queue = build_task_queue(event_bus=bus, visibility_timeout_seconds=5.0)
    executor = WorkflowEngineTaskExecutor(engine=engine, queue=task_queue)
    worker = build_worker(
        worker_id="worker-1", queue=task_queue, executor=executor, poll_interval_seconds=0.02, state_manager=state_manager
    )

    router = build_intent_router()
    router.add_route(WorkflowRoute(workflow_id="build_feature", intent_names=["build_feature"], keywords=["build"]))
    dispatcher = TaskQueueDispatcher(queue=task_queue)

    commander = build_commander(
        event_bus=bus,
        intent_classifier=router,
        workflow_resolver=router,
        agent_resolver=FakeAgentResolver([]),
        tool_resolver=FakeToolResolver([]),
        memory_resolver=FakeMemoryResolver(MemoryRequirement(scope="session", keys=[])),
        approval_policy=FakeApprovalPolicy(ApprovalDecision(required=False)),
        task_dispatcher=dispatcher,
        task_timeout_seconds=5.0,
    )
    team_builder = build_team_builder(memory_manager=memory_manager)
    mission_system = build_mission_system(commander=commander, intent_router=router, event_bus=bus, team_builder=team_builder)

    await worker.start()
    try:
        mission = await mission_system.create_mission(
            goal="please build the new feature", required_capabilities=["code_generation"]
        )
        await mission_system.assign_team(mission.id)
        result = await mission_system.execute_mission(mission.id)
        assert result.status == "completed"
    finally:
        await worker.stop()

    # -- Mission-level logs (#4): everything correlated via the
    # correlation_id=mission.id convention (Mission System -> Commander
    # -> TaskQueueDispatcher -> Task Queue's own published events all
    # share that one correlation_id). Workflow Engine deliberately does
    # NOT participate in this chain -- WorkflowRun mints its own fresh
    # run.id and every event it publishes is correlated by that, not by
    # whatever correlation_id the task that triggered it happened to
    # carry. That's an intentional, documented boundary (see the
    # module README's "known gaps" section), not a bug: workflow-level
    # events are reachable instead via workflow_run_id, bridged through
    # Task Queue's QueuedTask.workflow_run_id below. --
    mission_logs = await logging_system.list_by_mission(mission.id)
    assert len(mission_logs) > 3
    assert any(e.source_module == "mission_system" for e in mission_logs)
    assert any(e.source_module == "commander" for e in mission_logs)
    assert any(e.source_module == "task_queue" for e in mission_logs)
    assert not any(e.source_module == "workflow_engine" for e in mission_logs)

    # -- Workflow-level logs (#5): reached via workflow_run_id, not via
    # mission-level correlation -- see the note above. --
    mission_task = (await task_queue.list_tasks_for_mission(mission.id))[0]
    workflow_logs = await logging_system.list_by_workflow_run(mission_task.workflow_run_id)
    assert len(workflow_logs) > 0
    assert all(e.source_module == "workflow_engine" for e in workflow_logs)

    # -- Task-level logs (#6) --
    task_logs = await logging_system.list_by_task(mission_task.id)
    assert len(task_logs) > 0
    assert any(e.event_type == "task.completed" for e in task_logs)

    # -- Provider/tool logs (#7) --
    tool_logs = await logging_system.list_by_tool("mock_research")
    assert len(tool_logs) > 0

    # -- Health/status logs (#9) --
    health_logs = await logging_system.list_health_logs()
    assert any(e.source_module == "state_manager" for e in health_logs)

    # -- Error logs (#8): none expected on the happy path --
    assert await logging_system.list_errors() == []

    # -- Replay (#12): the mission's own correlation_id (= mission.id)
    # reconstructs the whole run in chronological order. --
    replayed = await logging_system.replay(mission.id)
    timestamps = [e.captured_at for e in replayed]
    assert timestamps == sorted(timestamps)
    rendered = logging_system.render_replay(replayed)
    assert "mission_system" in rendered and "commander" in rendered

    # -- Export (#13) --
    exported = await logging_system.export(mission_id=mission.id)
    assert len(exported) == len(mission_logs)
    assert isinstance(exported[0]["correlation_id"], str)


async def test_a_failure_produces_error_logs_findable_by_severity():
    bus = InMemoryEventBus()
    logging_system = build_logging_system(event_bus=bus)
    await logging_system.start()

    engine = build_workflow_engine(event_bus=bus)
    engine.register_workflow(
        WorkflowDefinition(
            workflow_id="always_fails",
            name="always_fails",
            steps=[
                StepDefinition(
                    name="a", kind="tool_call", tool_name="unconfigured",
                    retry_policy=RetryPolicy(max_attempts=1, backoff_base_seconds=0),
                )
            ],
        )
    )

    run = await engine.start_run("always_fails")
    assert run.status == "failed"

    errors = await logging_system.list_errors()
    assert any(e.event_type == "workflow_engine.step.failed" for e in errors)
    assert any(e.event_type == "workflow_engine.run.failed" for e in errors)
