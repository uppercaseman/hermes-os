"""Integration test: a real Commander, wired to a real WorkflowEngine
via WorkflowEngineTaskDispatcher, running a real (if simple) workflow
end to end. Every OTHER Commander collaborator (intent classifier,
agent/tool/memory resolvers, approval policy) is still a test fake --
only the workflow-resolution -> dispatch -> execution path is real,
which is exactly the seam this task is about proving.
"""
from __future__ import annotations

from hermes.core.commander.interface import build_commander
from hermes.core.commander.models import (
    AgentRequirement,
    ApprovalDecision,
    IncomingRequest,
    Intent,
    MemoryRequirement,
    ToolRequirement,
    WorkflowPlan,
)
from hermes.core.commander.tests.fakes import (
    FakeAgentResolver,
    FakeApprovalPolicy,
    FakeIntentClassifier,
    FakeMemoryResolver,
    FakeToolResolver,
    FakeWorkflowResolver,
)
from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.core.supervisor.policy import RetryPolicy
from hermes.modules.workflow_engine.commander_bridge import WorkflowEngineTaskDispatcher
from hermes.modules.workflow_engine.interface import build_workflow_engine
from hermes.modules.workflow_engine.models import StepDefinition, WorkflowDefinition
from hermes.modules.workflow_engine.templates import sequential_template


def _commander_kwargs(bus, workflow_plan):
    return dict(
        event_bus=bus,
        intent_classifier=FakeIntentClassifier(Intent(name="run_workflow", confidence=0.9)),
        workflow_resolver=FakeWorkflowResolver(workflow_plan),
        agent_resolver=FakeAgentResolver([AgentRequirement(agent_name="a", role="primary")]),
        tool_resolver=FakeToolResolver([ToolRequirement(tool_name="t", reason="r")]),
        memory_resolver=FakeMemoryResolver(MemoryRequirement(scope="session", keys=[])),
        approval_policy=FakeApprovalPolicy(ApprovalDecision(required=False)),
        retry_policy=RetryPolicy(max_attempts=2, backoff_base_seconds=0, backoff_multiplier=1),
        task_timeout_seconds=5.0,
    )


async def test_commander_dispatches_a_single_task_that_runs_the_whole_workflow():
    bus = InMemoryEventBus()
    engine = build_workflow_engine(event_bus=bus)
    engine.register_workflow(sequential_template("greet_workflow", "Greet", ["say_hello", "say_goodbye"]))
    dispatcher = WorkflowEngineTaskDispatcher(engine=engine, event_bus=bus)

    # steps=[] is the seam: Commander's Plan.build_tasks() dispatches
    # exactly one task, keyed by `name`, instead of one per step.
    workflow_plan = WorkflowPlan(workflow_id="greet_workflow", name="greet_workflow", steps=[])
    kwargs = _commander_kwargs(bus, workflow_plan)
    kwargs["task_dispatcher"] = dispatcher
    commander = build_commander(**kwargs)

    response = await commander.handle_request(IncomingRequest(raw_input="say hi", requester="user-1"))

    assert response.status == "completed"
    assert len(response.task_results) == 1
    assert response.task_results[0].status == "completed"


async def test_commander_sees_a_failed_task_when_the_workflow_run_fails():
    bus = InMemoryEventBus()
    engine = build_workflow_engine(event_bus=bus)

    engine.register_workflow(
        WorkflowDefinition(
            workflow_id="always_fails",
            name="always_fails",
            steps=[StepDefinition(name="a", kind="tool_call", tool_name="unconfigured")],  # no ToolManager on the engine
        )
    )
    dispatcher = WorkflowEngineTaskDispatcher(engine=engine, event_bus=bus)

    workflow_plan = WorkflowPlan(workflow_id="always_fails", name="always_fails", steps=[])
    kwargs = _commander_kwargs(bus, workflow_plan)
    kwargs["task_dispatcher"] = dispatcher
    kwargs["retry_policy"] = RetryPolicy(max_attempts=1, backoff_base_seconds=0)  # don't retry-multiply in this test
    commander = build_commander(**kwargs)

    response = await commander.handle_request(IncomingRequest(raw_input="do it", requester="user-1"))

    assert response.status == "failed"
    assert response.task_results[0].status == "failed"


async def test_workflow_engine_never_dispatches_or_sequences_on_commanders_behalf():
    """Documents the actual boundary: the bridge only ever calls
    start_run ONCE per Commander task, regardless of how many internal
    steps the workflow has -- Commander's dispatch count must stay at
    exactly one no matter how the workflow definition grows."""
    bus = InMemoryEventBus()
    engine = build_workflow_engine(event_bus=bus)
    engine.register_workflow(sequential_template("big_workflow", "Big", [f"step{i}" for i in range(10)]))
    dispatcher = WorkflowEngineTaskDispatcher(engine=engine, event_bus=bus)

    workflow_plan = WorkflowPlan(workflow_id="big_workflow", name="big_workflow", steps=[])
    kwargs = _commander_kwargs(bus, workflow_plan)
    kwargs["task_dispatcher"] = dispatcher
    commander = build_commander(**kwargs)

    response = await commander.handle_request(IncomingRequest(raw_input="go", requester="user-1"))

    assert len(response.task_results) == 1  # one Commander-level task no matter the step count
    assert response.status == "completed"
