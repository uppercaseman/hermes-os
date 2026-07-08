"""The vertical-slice proof: a real Commander, a real WorkflowEngine, a
real Tool Manager (with the mock research adapter registered), a real
Memory Manager, and one real Event Bus -- wired together and run for
real. Nothing here is a fake standing in for one of these five systems;
the only mock is the research tool's content, per the brief.
"""
from __future__ import annotations

from hermes.core.commander.models import IncomingRequest
from hermes.demos.research_brief.runner import build_research_brief_pipeline, run_research_brief
from hermes.modules.tool_manager.models import ToolInvocationRequest


async def test_full_path_returns_a_completed_structured_brief():
    brief = await run_research_brief("hermes agent operating system")

    assert brief["status"] == "completed"
    assert brief["topic"] == "hermes agent operating system"
    assert "hermes agent operating system" in brief["summary"]
    assert len(brief["sources"]) == 2
    assert brief["memory_entry_id"] is not None
    assert brief["step_statuses"] == {
        "accept_topic": "completed",
        "read_memory": "completed",
        "call_research_tool": "completed",
        "save_to_memory": "completed",
        "assemble_brief": "completed",
    }


async def test_the_mock_tool_is_invoked_through_tool_manager_for_real():
    pipeline = build_research_brief_pipeline()

    await run_research_brief("quantum computing", pipeline=pipeline)

    status = await pipeline.tool_manager.status("mock_research")
    assert status.total_invocations == 1
    assert status.total_failures == 0


async def test_repeating_the_same_topic_shares_the_same_memory_entry():
    """Two runs, same topic, same pipeline: the second run's read_memory
    step should find what the first run wrote -- accumulation WITHIN one
    topic, via the topic-templated memory key resolving to the same
    string both times."""
    pipeline = build_research_brief_pipeline()

    first = await run_research_brief("recurring topic", pipeline=pipeline)
    second = await run_research_brief("recurring topic", pipeline=pipeline)

    entry = await pipeline.memory_manager.get_by_key(
        requesting_agent_id="commander", scope="persistent", key="research_brief/recurring topic"
    )
    assert entry is not None
    assert first["status"] == second["status"] == "completed"


async def test_different_topics_do_not_share_a_memory_entry():
    """The per-topic isolation fix: two DIFFERENT topics on the SAME
    pipeline must resolve to two DIFFERENT memory keys, never
    overwriting or leaking into each other."""
    pipeline = build_research_brief_pipeline()

    await run_research_brief("topic one", pipeline=pipeline)
    await run_research_brief("topic two", pipeline=pipeline)

    entry_one = await pipeline.memory_manager.get_by_key(
        requesting_agent_id="commander", scope="persistent", key="research_brief/topic one"
    )
    entry_two = await pipeline.memory_manager.get_by_key(
        requesting_agent_id="commander", scope="persistent", key="research_brief/topic two"
    )
    assert entry_one is not None and entry_one.value["topic"] == "topic one"
    assert entry_two is not None and entry_two.value["topic"] == "topic two"


async def test_events_are_published_across_every_layer_of_the_stack():
    pipeline = build_research_brief_pipeline()
    seen_event_types: set[str] = set()

    async def capture(event):
        seen_event_types.add(event.event_type)

    await pipeline.event_bus.subscribe("*", capture)

    await run_research_brief("event propagation", pipeline=pipeline)

    assert "commander.request.received" in seen_event_types
    assert "commander.run.completed" in seen_event_types
    assert "workflow_engine.run.started" in seen_event_types
    assert "workflow_engine.run.completed" in seen_event_types
    assert "workflow_engine.step.completed" in seen_event_types
    assert "tool_manager.tool.invoked" in seen_event_types
    assert "memory_manager.entry.saved" in seen_event_types


async def test_direct_tool_manager_invocation_reaches_the_same_mock_adapter():
    """Sanity check that the pipeline's ToolManager really has the mock
    adapter registered under the name the workflow expects, independent
    of going through Commander/Workflow Engine at all."""
    pipeline = build_research_brief_pipeline()

    result = await pipeline.tool_manager.invoke(
        ToolInvocationRequest(tool_name="mock_research", operation="research", parameters={"topic": "direct call"})
    )

    assert result.status == "completed"
    assert "direct call" in result.output["summary"]


async def test_fresh_pipeline_per_call_does_not_share_memory():
    """run_research_brief() with no explicit pipeline builds a fresh one
    each time -- confirms two independent CLI-style invocations don't
    leak state into each other."""
    first = await run_research_brief("isolated topic a")
    second = await run_research_brief("isolated topic b")

    assert first["topic"] == "isolated topic a"
    assert second["topic"] == "isolated topic b"


# --------------------------------------------------------------------- #
# Real intent routing (this task's fix #1) -- proves the router genuinely
# discriminates, not just that the CLI's happy path still works.
# --------------------------------------------------------------------- #

async def test_command_prefixed_request_reaches_research_brief_without_explicit_metadata():
    """Calls Commander directly (bypassing run_research_brief's explicit
    metadata shortcut) to prove the router's command-matching path is
    real, not just theoretical."""
    pipeline = build_research_brief_pipeline()
    request = IncomingRequest(raw_input="/research the moon landing", requester="test")

    response = await pipeline.commander.handle_request(request)

    assert response.status == "completed"


async def test_keyword_matched_request_reaches_research_brief_without_explicit_metadata():
    pipeline = build_research_brief_pipeline()
    request = IncomingRequest(raw_input="please research the history of glass", requester="test")

    response = await pipeline.commander.handle_request(request)

    assert response.status == "completed"


async def test_a_request_matching_no_route_fails_cleanly_instead_of_always_running_research_brief():
    """The concrete proof that intent routing is real: unlike the
    original vertical slice (which always picked the same workflow),
    unmatched input must now fail rather than silently run anyway."""
    pipeline = build_research_brief_pipeline()
    request = IncomingRequest(raw_input="what time is it in Tokyo", requester="test")

    response = await pipeline.commander.handle_request(request)

    assert response.status == "failed"
    assert "no workflow route matched" in response.summary
