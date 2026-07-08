from hermes.core.commander.events import REQUEST_RECEIVED, TASK_RETRY_SCHEDULED
from hermes.core.commander.interface import build_commander
from hermes.core.commander.models import ApprovalDecision, IncomingRequest, WorkflowPlan
from hermes.core.commander.tests.fakes import (
    FailingIntentClassifier,
    FakeApprovalPolicy,
    FakeWorkflowResolver,
    ScriptedTaskDispatcher,
    SlowIntentClassifier,
    SlowWorkflowResolver,
)
from hermes.core.event_bus.models import Event
from hermes.core.supervisor.policy import RetryPolicy


async def test_happy_path_returns_completed_response(commander_kwargs, bus):
    kwargs = dict(commander_kwargs, task_dispatcher=ScriptedTaskDispatcher(bus))
    commander = build_commander(**kwargs)
    request = IncomingRequest(raw_input="what's the weather", requester="user-1")

    response = await commander.handle_request(request)

    assert response.status == "completed"
    assert response.request_id == request.id
    assert len(response.task_results) == 2  # two workflow steps
    assert all(r.status == "completed" for r in response.task_results)


async def test_request_received_event_is_published_first(commander_kwargs, bus):
    received: list[Event] = []

    async def capture(event: Event) -> None:
        received.append(event)

    await bus.subscribe(REQUEST_RECEIVED, capture)
    kwargs = dict(commander_kwargs, task_dispatcher=ScriptedTaskDispatcher(bus))
    commander = build_commander(**kwargs)
    request = IncomingRequest(raw_input="hello", requester="user-1")

    await commander.handle_request(request)

    assert len(received) == 1
    assert received[0].payload["request_id"] == str(request.id)


async def test_approval_required_halts_before_dispatch(commander_kwargs, dispatcher):
    kwargs = dict(
        commander_kwargs,
        approval_policy=FakeApprovalPolicy(ApprovalDecision(required=True, approved=False, reason="cost")),
    )
    commander = build_commander(**kwargs)
    request = IncomingRequest(raw_input="deploy to prod", requester="user-1")

    response = await commander.handle_request(request)

    assert response.status == "awaiting_approval"
    assert response.task_results == []
    assert dispatcher.dispatched == []  # dispatcher was never reached


async def test_resume_after_approval_dispatches(commander_kwargs, bus):
    kwargs = dict(
        commander_kwargs,
        approval_policy=FakeApprovalPolicy(ApprovalDecision(required=True, approved=False)),
        task_dispatcher=ScriptedTaskDispatcher(bus),
    )
    commander = build_commander(**kwargs)
    request = IncomingRequest(raw_input="deploy to prod", requester="user-1")

    pending = await commander.handle_request(request)
    assert pending.status == "awaiting_approval"

    approval = ApprovalDecision(required=True, approved=True, approver="ops-lead")
    final = await commander.resume_after_approval(request.id, pending.plan, approval)

    assert final.status == "completed"


async def test_resume_after_denied_approval_returns_failed(commander_kwargs):
    kwargs = dict(
        commander_kwargs,
        approval_policy=FakeApprovalPolicy(ApprovalDecision(required=True, approved=False)),
    )
    commander = build_commander(**kwargs)
    request = IncomingRequest(raw_input="delete prod db", requester="user-1")

    pending = await commander.handle_request(request)
    denial = ApprovalDecision(required=True, approved=False, reason="too risky", approver="ops-lead")
    final = await commander.resume_after_approval(request.id, pending.plan, denial)

    assert final.status == "failed"
    assert "too risky" in final.summary


async def test_planning_failure_returns_failed_response_without_raising(commander_kwargs):
    kwargs = dict(commander_kwargs, intent_classifier=FailingIntentClassifier())
    commander = build_commander(**kwargs)
    request = IncomingRequest(raw_input="???", requester="user-1")

    response = await commander.handle_request(request)

    assert response.status == "failed"
    assert response.plan is None
    assert "blew up" in response.summary


async def test_failed_task_is_retried_then_succeeds(commander_kwargs, bus, sample_workflow):
    single_step = sample_workflow.model_copy(update={"steps": ["only-step"]})
    scripted_dispatcher = ScriptedTaskDispatcher(bus, outcomes=["failed", "completed"])
    kwargs = dict(
        commander_kwargs,
        workflow_resolver=FakeWorkflowResolver(single_step),
        task_dispatcher=scripted_dispatcher,
    )
    commander = build_commander(**kwargs)
    request = IncomingRequest(raw_input="retry me", requester="user-1")

    retries: list[Event] = []

    async def capture(event: Event) -> None:
        retries.append(event)

    await bus.subscribe(TASK_RETRY_SCHEDULED, capture)

    response = await commander.handle_request(request)

    assert response.status == "completed"
    assert len(scripted_dispatcher.dispatched) == 2  # first attempt + one retry
    assert len(retries) == 1


async def test_task_exhausts_retries_and_run_fails(commander_kwargs, bus, sample_workflow):
    single_step = sample_workflow.model_copy(update={"steps": ["only-step"]})
    kwargs = dict(
        commander_kwargs,
        workflow_resolver=FakeWorkflowResolver(single_step),
        task_dispatcher=ScriptedTaskDispatcher(bus, outcomes=["failed", "failed", "failed"]),
        retry_policy=RetryPolicy(max_attempts=3, backoff_base_seconds=0, backoff_multiplier=1),
    )
    commander = build_commander(**kwargs)
    request = IncomingRequest(raw_input="always fails", requester="user-1")

    response = await commander.handle_request(request)

    assert response.status == "failed"
    assert response.task_results[0].status == "failed"


async def test_task_timeout_reports_failed_result(commander_kwargs, sample_workflow, dispatcher):
    single_step = sample_workflow.model_copy(update={"steps": ["only-step"]})
    kwargs = dict(
        commander_kwargs,
        workflow_resolver=FakeWorkflowResolver(single_step),
        task_dispatcher=dispatcher,  # RecordingTaskDispatcher never completes the task
        task_timeout_seconds=0.05,
        retry_policy=RetryPolicy(max_attempts=1, backoff_base_seconds=0),
    )
    commander = build_commander(**kwargs)
    request = IncomingRequest(raw_input="never responds", requester="user-1")

    response = await commander.handle_request(request)

    assert response.status == "failed"
    assert "timed out" in (response.task_results[0].error or "")


# --------------------------------------------------------------------- #
# Planning-phase timeout (architecture fix #1)
# --------------------------------------------------------------------- #

async def test_hung_intent_classifier_fails_fast_instead_of_hanging(commander_kwargs):
    kwargs = dict(
        commander_kwargs,
        intent_classifier=SlowIntentClassifier(delay_seconds=10.0),
        planning_timeout_seconds=0.05,
    )
    commander = build_commander(**kwargs)
    request = IncomingRequest(raw_input="hang please", requester="user-1")

    response = await commander.handle_request(request)

    assert response.status == "failed"
    assert response.plan is None
    assert "intent_classification timed out" in response.summary


async def test_hung_workflow_resolver_fails_fast_naming_that_stage(commander_kwargs):
    kwargs = dict(
        commander_kwargs,
        workflow_resolver=SlowWorkflowResolver(WorkflowPlan(workflow_id="wf", name="n"), delay_seconds=10.0),
        planning_timeout_seconds=0.05,
    )
    commander = build_commander(**kwargs)
    request = IncomingRequest(raw_input="hang please", requester="user-1")

    response = await commander.handle_request(request)

    assert response.status == "failed"
    assert "workflow_resolution timed out" in response.summary


async def test_planning_within_timeout_still_succeeds(commander_kwargs, bus):
    """Regression guard: adding the timeout must not make a normal, fast
    plan fail spuriously."""
    kwargs = dict(
        commander_kwargs,
        intent_classifier=SlowIntentClassifier(delay_seconds=0.01),
        planning_timeout_seconds=5.0,
        task_dispatcher=ScriptedTaskDispatcher(bus),
    )
    commander = build_commander(**kwargs)
    request = IncomingRequest(raw_input="quick", requester="user-1")

    response = await commander.handle_request(request)

    assert response.status == "completed"
