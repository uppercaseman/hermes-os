from hermes.core.commander.errors import PlanningTimeoutError


def test_planning_timeout_error_message_names_stage_and_duration():
    error = PlanningTimeoutError("workflow_resolution", 12.5)

    assert error.stage == "workflow_resolution"
    assert error.timeout_seconds == 12.5
    assert str(error) == "workflow_resolution timed out after 12.5s"
