from hermes.modules.intent_router.models import WorkflowRoute


def test_route_defaults_to_no_matching_criteria():
    route = WorkflowRoute(workflow_id="wf1")

    assert route.intent_names == []
    assert route.keywords == []
    assert route.command is None
    assert route.priority == 100
