import pytest

from hermes.core.commander.models import IncomingRequest
from hermes.modules.intent_router.errors import UnknownIntentError
from hermes.modules.intent_router.interface import build_intent_router
from hermes.modules.intent_router.models import WorkflowRoute


def _request(text: str, *, intent: str | None = None) -> IncomingRequest:
    metadata = {"intent": intent} if intent else {}
    return IncomingRequest(raw_input=text, requester="test", metadata=metadata)


async def test_explicit_intent_hint_matches_regardless_of_text():
    router = build_intent_router()
    router.add_route(WorkflowRoute(workflow_id="research_brief", intent_names=["research_brief"]))

    intent = await router.classify(_request("this text matches nothing else", intent="research_brief"))

    assert intent.name == "research_brief"
    assert intent.confidence == 1.0


async def test_command_prefix_matches_when_no_explicit_intent():
    router = build_intent_router()
    router.add_route(WorkflowRoute(workflow_id="research_brief", command="/research"))

    intent = await router.classify(_request("/research quantum computing"))

    assert intent.name == "research_brief"
    assert intent.confidence == 0.9


async def test_keyword_match_when_no_explicit_intent_or_command():
    router = build_intent_router()
    router.add_route(WorkflowRoute(workflow_id="research_brief", keywords=["research", "investigate"]))

    intent = await router.classify(_request("please research the history of tea"))

    assert intent.name == "research_brief"
    assert intent.confidence == 0.6


async def test_keyword_matching_is_case_insensitive():
    router = build_intent_router()
    router.add_route(WorkflowRoute(workflow_id="research_brief", keywords=["RESEARCH"]))

    intent = await router.classify(_request("Research something"))

    assert intent.name == "research_brief"


async def test_explicit_intent_beats_a_different_routes_command_or_keyword_match():
    router = build_intent_router()
    router.add_route(WorkflowRoute(workflow_id="other_workflow", command="/research", keywords=["research"]))
    router.add_route(WorkflowRoute(workflow_id="research_brief", intent_names=["research_brief"]))

    intent = await router.classify(_request("/research something", intent="research_brief"))

    assert intent.name == "research_brief"  # explicit hint wins even though the other route's command also matches


async def test_unmatched_request_classifies_as_unknown_with_no_default():
    router = build_intent_router()
    router.add_route(WorkflowRoute(workflow_id="research_brief", keywords=["research"]))

    intent = await router.classify(_request("what's the weather like"))

    assert intent.name == "unknown"
    assert intent.confidence == 0.0


async def test_resolving_an_unknown_intent_raises_without_a_default():
    router = build_intent_router()
    request = _request("what's the weather like")
    intent = await router.classify(request)

    with pytest.raises(UnknownIntentError):
        await router.resolve(intent, request)


async def test_unmatched_request_falls_back_to_configured_default():
    router = build_intent_router(default_workflow_id="fallback_workflow")
    request = _request("something totally unrelated")

    intent = await router.classify(request)
    plan = await router.resolve(intent, request)

    assert intent.confidence == 0.0
    assert plan.workflow_id == "fallback_workflow"


async def test_priority_breaks_ties_between_routes_matched_by_the_same_mechanism():
    router = build_intent_router()
    router.add_route(WorkflowRoute(workflow_id="low_priority", keywords=["research"], priority=50))
    router.add_route(WorkflowRoute(workflow_id="high_priority", keywords=["research"], priority=10))

    intent = await router.classify(_request("research this please"))

    assert intent.name == "high_priority"


async def test_resolve_returns_a_workflow_plan_with_empty_steps_for_the_commander_seam():
    """steps=[] is what makes Commander dispatch exactly one opaque task
    per Plan.build_tasks()'s existing fallback -- see the Workflow
    Engine's commander_bridge.py."""
    router = build_intent_router()
    router.add_route(WorkflowRoute(workflow_id="research_brief", keywords=["research"]))
    request = _request("research something")
    intent = await router.classify(request)

    plan = await router.resolve(intent, request)

    assert plan.workflow_id == "research_brief"
    assert plan.steps == []


async def test_classify_carries_the_raw_text_as_topic_in_slots():
    router = build_intent_router()
    router.add_route(WorkflowRoute(workflow_id="research_brief", keywords=["research"]))

    intent = await router.classify(_request("research the moon landing"))

    assert intent.slots["topic"] == "research the moon landing"
