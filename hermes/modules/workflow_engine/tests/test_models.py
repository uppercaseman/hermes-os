from hermes.modules.workflow_engine.models import StepDefinition, StepResult, WorkflowDefinition, WorkflowRun


def test_step_definition_defaults_to_noop_with_no_dependencies():
    step = StepDefinition(name="s1", kind="noop")

    assert step.depends_on == []
    assert step.condition is None
    assert step.timeout_seconds == 30.0


def test_workflow_definition_holds_its_steps():
    definition = WorkflowDefinition(workflow_id="wf1", name="Test", steps=[StepDefinition(name="a", kind="noop")])

    assert len(definition.steps) == 1


def test_step_result_defaults_to_pending():
    result = StepResult(name="a")

    assert result.status == "pending"
    assert result.attempts == 0


def test_workflow_run_defaults_to_running_with_no_step_results():
    run = WorkflowRun(workflow_id="wf1")

    assert run.status == "running"
    assert run.step_results == {}
    assert run.requesting_agent_id == "system"
