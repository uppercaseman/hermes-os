import uuid

import pytest
from pydantic import ValidationError

from hermes.core.commander.models import (
    AgentRequirement,
    ApprovalDecision,
    Intent,
    MemoryRequirement,
    Plan,
    ToolRequirement,
    WorkflowPlan,
)


def test_intent_confidence_must_be_between_zero_and_one():
    Intent(name="ok", confidence=0.5)
    with pytest.raises(ValidationError):
        Intent(name="bad", confidence=1.5)


def test_plan_build_tasks_creates_one_task_per_step():
    plan = Plan(
        request_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        intent=Intent(name="x", confidence=1.0),
        workflow=WorkflowPlan(workflow_id="wf", name="n", steps=["a", "b", "c"]),
        agents=[AgentRequirement(agent_name="a1", role="primary")],
        tools=[ToolRequirement(tool_name="t1", reason="r")],
        memory=MemoryRequirement(scope="session", keys=["k"]),
    )

    tasks = plan.build_tasks()

    assert len(tasks) == 3
    assert {t.payload["step"] for t in tasks} == {"a", "b", "c"}
    assert all(t.correlation_id == plan.correlation_id for t in tasks)


def test_plan_build_tasks_falls_back_to_workflow_name_with_no_steps():
    plan = Plan(
        request_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        intent=Intent(name="x", confidence=1.0),
        workflow=WorkflowPlan(workflow_id="wf", name="single_shot", steps=[]),
        agents=[],
        tools=[],
        memory=MemoryRequirement(scope="session", keys=[]),
    )

    tasks = plan.build_tasks()

    assert len(tasks) == 1
    assert tasks[0].payload["step"] == "single_shot"


def test_approval_decision_defaults_to_not_approved():
    decision = ApprovalDecision(required=True)
    assert decision.approved is False


def test_memory_requirement_rejects_invalid_scope():
    with pytest.raises(ValidationError):
        MemoryRequirement(scope="galaxy-wide", keys=[])
