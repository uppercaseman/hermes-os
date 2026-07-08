"""Integration test: real Commander, real Task Queue + Worker (driving
real Workflow Engine execution through the durable queue, not inline),
real Intent Router, real Mission System + Team Builder, real Memory
Manager, real State Manager, and one real Event Bus -- wired together
and run for real. Proves every required integration (Commander, Mission
System, Workflow Engine, Event Bus, State Manager) actually works end to
end through the durable queue, not just that each module works alone.
"""
from __future__ import annotations

import asyncio

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
from hermes.modules.memory_manager.interface import build_memory_manager
from hermes.modules.mission_system.interface import build_mission_system, build_team_builder
from hermes.modules.task_queue.commander_dispatcher import TaskQueueDispatcher
from hermes.modules.task_queue.interface import build_task_queue, build_worker
from hermes.modules.task_queue.workflow_executor import WorkflowEngineTaskExecutor
from hermes.modules.workflow_engine.interface import build_workflow_engine
from hermes.modules.workflow_engine.templates import sequential_template


async def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.02) -> None:
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return
        await asyncio.sleep(interval)
        elapsed += interval
    raise AssertionError("condition not met within timeout")


async def test_mission_executes_through_the_real_durable_queue_end_to_end():
    bus = InMemoryEventBus()
    state_manager = build_state_manager(event_bus=bus)
    memory_manager = build_memory_manager(event_bus=bus)

    engine = build_workflow_engine(event_bus=bus, memory_manager=memory_manager)
    engine.register_workflow(sequential_template("build_feature", "build_feature", ["design", "implement", "test"]))

    task_queue = build_task_queue(event_bus=bus, visibility_timeout_seconds=5.0)
    executor = WorkflowEngineTaskExecutor(engine=engine, queue=task_queue)
    worker = build_worker(
        worker_id="worker-1", queue=task_queue, executor=executor, poll_interval_seconds=0.02, state_manager=state_manager
    )

    router = build_intent_router()
    router.add_route(WorkflowRoute(workflow_id="build_feature", intent_names=["build_feature"], keywords=["build"]))

    dispatcher = TaskQueueDispatcher(queue=task_queue)  # the REAL durable dispatcher, not the inline one

    commander = build_commander(
        event_bus=bus,
        intent_classifier=router,
        workflow_resolver=router,
        agent_resolver=FakeAgentResolver([]),
        tool_resolver=FakeToolResolver([]),
        memory_resolver=FakeMemoryResolver(MemoryRequirement(scope="session", keys=[])),
        approval_policy=FakeApprovalPolicy(ApprovalDecision(required=False)),
        task_dispatcher=dispatcher,
        task_timeout_seconds=5.0,  # generous: the worker polls asynchronously, not inline
    )

    team_builder = build_team_builder(memory_manager=memory_manager)
    mission_system = build_mission_system(
        commander=commander, intent_router=router, event_bus=bus, team_builder=team_builder
    )

    events_seen: list[str] = []

    async def capture(event):
        events_seen.append(event.event_type)

    await bus.subscribe("*", capture)

    await worker.start()  # must be polling BEFORE Commander dispatches, or nothing claims the task
    try:
        mission = await mission_system.create_mission(
            goal="build the new feature", required_capabilities=["code_generation"]
        )
        await mission_system.assign_team(mission.id)

        result = await mission_system.execute_mission(mission.id)
        assert result.status == "completed"
    finally:
        await worker.stop()

    # Mission-level tracking (#13): the task the dispatcher enqueued is
    # findable by mission_id, via the correlation_id=mission.id
    # convention set up in Mission System's execute_mission().
    mission_tasks = await task_queue.list_tasks_for_mission(mission.id)
    assert len(mission_tasks) == 1
    assert mission_tasks[0].status == "completed"

    # Workflow-level tracking (#14): the task was retroactively tagged
    # with the WorkflowRun's id once WorkflowEngineTaskExecutor learned it.
    assert mission_tasks[0].workflow_run_id is not None
    run_tasks = await task_queue.list_tasks_for_workflow_run(mission_tasks[0].workflow_run_id)
    assert [t.id for t in run_tasks] == [mission_tasks[0].id]

    # State Manager integration: the worker reported both idle and busy
    # heartbeats over the course of polling and executing.
    assert state_manager.get_state("worker-1") in ("healthy", "busy", "idle")

    # Every required integration published a real event.
    assert "commander.request.received" in events_seen
    assert "commander.run.completed" in events_seen
    assert "task_queue.task.enqueued" in events_seen
    assert "task_queue.task.claimed" in events_seen
    assert "task.completed" in events_seen  # the exact string Commander listens for
    assert "workflow_engine.run.started" in events_seen
    assert "workflow_engine.run.completed" in events_seen
    assert "mission_system.mission.completed" in events_seen
    assert "state_manager.module.state_reported" in events_seen


async def test_a_failing_workflow_dead_letters_the_task_and_fails_the_mission():
    bus = InMemoryEventBus()
    engine = build_workflow_engine(event_bus=bus)  # no tool_manager/memory_manager -- steps requiring them will fail
    from hermes.modules.workflow_engine.models import StepDefinition, WorkflowDefinition

    engine.register_workflow(
        WorkflowDefinition(
            workflow_id="always_fails",
            name="always_fails",
            steps=[
                StepDefinition(
                    name="a",
                    kind="tool_call",
                    tool_name="unconfigured",
                    retry_policy=RetryPolicy(max_attempts=1, backoff_base_seconds=0),
                )
            ],
        )
    )

    task_queue = build_task_queue(event_bus=bus, visibility_timeout_seconds=5.0)
    executor = WorkflowEngineTaskExecutor(engine=engine, queue=task_queue)
    worker = build_worker(worker_id="worker-1", queue=task_queue, executor=executor, poll_interval_seconds=0.02)

    router = build_intent_router()
    router.add_route(WorkflowRoute(workflow_id="always_fails", intent_names=["always_fails"]))
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
        retry_policy=RetryPolicy(max_attempts=1, backoff_base_seconds=0),
    )
    team_builder = build_team_builder()
    mission_system = build_mission_system(commander=commander, intent_router=router, event_bus=bus, team_builder=team_builder)

    await worker.start()
    try:
        mission = await mission_system.create_mission(goal="build_feature", required_workflows=["always_fails"])
        await mission_system.assign_team(mission.id)
        result = await mission_system.execute_mission(mission.id)
        assert result.status == "failed"
    finally:
        await worker.stop()

    dead_letter = await task_queue.list_dead_letter_tasks()
    assert len(dead_letter) == 1
