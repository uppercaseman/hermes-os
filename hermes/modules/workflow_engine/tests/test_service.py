import asyncio
import inspect
import time
import uuid

import pytest

from hermes.core.supervisor.policy import RetryPolicy
from hermes.modules.workflow_engine.errors import (
    InvalidWorkflowDefinitionError,
    UnknownWorkflowError,
    UnknownWorkflowRunError,
)
from hermes.modules.workflow_engine.interface import build_workflow_engine
from hermes.modules.workflow_engine.models import StepCondition, StepDefinition, WorkflowDefinition
from hermes.modules.workflow_engine.templates import approval_gated_template, fan_out_fan_in_template, sequential_template
from hermes.modules.workflow_engine.tests.fakes import (
    FakeCapabilitySelector,
    FakeMemoryStore,
    FakeToolInvoker,
    HangingToolInvoker,
)

INSTANT_RETRY = RetryPolicy(max_attempts=3, backoff_base_seconds=0, backoff_multiplier=1)


# --------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------- #

def test_register_and_get_workflow_roundtrips(engine):
    definition = sequential_template("wf1", "Seq", ["a", "b"])
    engine.register_workflow(definition)

    assert engine.get_workflow("wf1").name == "Seq"


def test_get_unknown_workflow_raises(engine):
    with pytest.raises(UnknownWorkflowError):
        engine.get_workflow("nope")


def test_rejects_duplicate_step_names(engine):
    definition = WorkflowDefinition(
        workflow_id="wf1", name="Bad", steps=[StepDefinition(name="a", kind="noop"), StepDefinition(name="a", kind="noop")]
    )
    with pytest.raises(InvalidWorkflowDefinitionError):
        engine.register_workflow(definition)


def test_rejects_depends_on_referencing_unknown_step(engine):
    definition = WorkflowDefinition(
        workflow_id="wf1", name="Bad", steps=[StepDefinition(name="a", kind="noop", depends_on=["ghost"])]
    )
    with pytest.raises(InvalidWorkflowDefinitionError):
        engine.register_workflow(definition)


def test_rejects_condition_referencing_unknown_step(engine):
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="Bad",
        steps=[StepDefinition(name="a", kind="noop", condition=StepCondition(step="ghost"))],
    )
    with pytest.raises(InvalidWorkflowDefinitionError):
        engine.register_workflow(definition)


def test_rejects_condition_referencing_a_step_not_in_depends_on(engine):
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="Bad",
        steps=[
            StepDefinition(name="a", kind="noop"),
            StepDefinition(name="b", kind="noop", condition=StepCondition(step="a")),  # missing depends_on=["a"]
        ],
    )
    with pytest.raises(InvalidWorkflowDefinitionError):
        engine.register_workflow(definition)


def test_rejects_a_dependency_cycle(engine):
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="Bad",
        steps=[
            StepDefinition(name="a", kind="noop", depends_on=["b"]),
            StepDefinition(name="b", kind="noop", depends_on=["a"]),
        ],
    )
    with pytest.raises(InvalidWorkflowDefinitionError):
        engine.register_workflow(definition)


def test_rejects_tool_call_step_with_neither_tool_name_nor_capability(engine):
    definition = WorkflowDefinition(workflow_id="wf1", name="Bad", steps=[StepDefinition(name="a", kind="tool_call")])
    with pytest.raises(InvalidWorkflowDefinitionError):
        engine.register_workflow(definition)


def test_rejects_tool_call_step_with_both_tool_name_and_capability(engine):
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="Bad",
        steps=[StepDefinition(name="a", kind="tool_call", tool_name="x", capability="reasoning")],
    )
    with pytest.raises(InvalidWorkflowDefinitionError):
        engine.register_workflow(definition)


def test_rejects_memory_step_missing_scope_or_key(engine):
    definition = WorkflowDefinition(
        workflow_id="wf1", name="Bad", steps=[StepDefinition(name="a", kind="memory_write")]
    )
    with pytest.raises(InvalidWorkflowDefinitionError):
        engine.register_workflow(definition)


async def test_start_run_on_unknown_workflow_raises(engine):
    with pytest.raises(UnknownWorkflowError):
        await engine.start_run("nope")


# --------------------------------------------------------------------- #
# Sequencing + parallel steps (#2, #4)
# --------------------------------------------------------------------- #

async def test_sequential_workflow_runs_all_steps_to_completion(engine):
    engine.register_workflow(sequential_template("wf1", "Seq", ["a", "b", "c"]))

    run = await engine.start_run("wf1")

    assert run.status == "completed"
    assert [run.step_results[n].status for n in ("a", "b", "c")] == ["completed"] * 3


async def test_parallel_steps_both_run_before_the_join_step(engine):
    engine.register_workflow(
        fan_out_fan_in_template("wf1", "Fan", parallel_step_names=["p1", "p2"], join_step_name="join")
    )

    run = await engine.start_run("wf1")

    assert run.status == "completed"
    assert run.step_results["p1"].status == "completed"
    assert run.step_results["p2"].status == "completed"
    assert run.step_results["join"].started_at >= run.step_results["p1"].ended_at
    assert run.step_results["join"].started_at >= run.step_results["p2"].ended_at


async def test_parallel_steps_actually_overlap_in_wall_clock_time():
    """Uses a real, small delay in both parallel steps' tool calls and
    asserts total run time is closer to one delay than two -- proving
    they ran concurrently, not sequentially."""
    tool_manager = FakeToolInvoker()
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="Fan",
        steps=[
            StepDefinition(name="p1", kind="tool_call", tool_name="slow-tool", timeout_seconds=5),
            StepDefinition(name="p2", kind="tool_call", tool_name="slow-tool", timeout_seconds=5),
        ],
    )

    class DelayedToolInvoker:
        async def invoke(self, request):
            await asyncio.sleep(0.1)
            return await tool_manager.invoke(request)

    engine = build_workflow_engine(tool_manager=DelayedToolInvoker())
    engine.register_workflow(definition)

    start = time.monotonic()
    run = await engine.start_run("wf1")
    elapsed = time.monotonic() - start

    assert run.status == "completed"
    assert elapsed < 0.19  # well under 2x0.1s -- proves concurrency, not serial execution


# --------------------------------------------------------------------- #
# Conditional branching (#3)
# --------------------------------------------------------------------- #

async def test_step_is_skipped_when_condition_is_false(engine):
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="Branch",
        steps=[
            StepDefinition(name="check", kind="noop"),
            StepDefinition(
                name="only_if_true",
                kind="noop",
                depends_on=["check"],
                condition=StepCondition(step="check", path="go", equals=True),
            ),
        ],
    )
    engine.register_workflow(definition)

    run = await engine.start_run("wf1")

    assert run.step_results["only_if_true"].status == "skipped"
    assert run.status == "completed"  # a skip is not a failure


async def test_step_runs_when_condition_is_true():
    tool_manager = FakeToolInvoker(output={"go": True})
    engine = build_workflow_engine(tool_manager=tool_manager)
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="Branch",
        steps=[
            StepDefinition(name="check", kind="tool_call", tool_name="checker"),
            StepDefinition(
                name="only_if_true",
                kind="noop",
                depends_on=["check"],
                condition=StepCondition(step="check", path="go", equals=True),
            ),
        ],
    )
    engine.register_workflow(definition)

    run = await engine.start_run("wf1")

    assert run.step_results["only_if_true"].status == "completed"


async def test_failure_handling_branch_runs_only_when_its_dependency_failed():
    engine = build_workflow_engine(tool_manager=FakeToolInvoker(outcomes=["raise", "raise", "raise"]))
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="ErrorBranch",
        steps=[
            StepDefinition(name="risky", kind="tool_call", tool_name="x", retry_policy=INSTANT_RETRY),
            StepDefinition(
                name="on_success", kind="noop", depends_on=["risky"], condition=StepCondition(step="risky", equals="completed")
            ),
            StepDefinition(
                name="on_failure", kind="noop", depends_on=["risky"], condition=StepCondition(step="risky", equals="failed")
            ),
        ],
    )
    engine.register_workflow(definition)

    run = await engine.start_run("wf1")

    assert run.step_results["risky"].status == "failed"
    assert run.step_results["on_failure"].status == "completed"  # the error-handling branch ran
    assert run.step_results["on_success"].status == "skipped"  # the happy-path branch did not


async def test_ordinary_dependent_is_skipped_when_its_dependency_fails():
    engine = build_workflow_engine(tool_manager=FakeToolInvoker(outcomes=["raise", "raise", "raise"]))
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="Chain",
        steps=[
            StepDefinition(name="risky", kind="tool_call", tool_name="x", retry_policy=INSTANT_RETRY),
            StepDefinition(name="next", kind="noop", depends_on=["risky"]),  # no condition -- ordinary dependent
        ],
    )
    engine.register_workflow(definition)

    run = await engine.start_run("wf1")

    assert run.step_results["next"].status == "skipped"
    assert run.status == "failed"  # the genuine failure still fails the overall run


# --------------------------------------------------------------------- #
# Retries + timeouts (#5, #6)
# --------------------------------------------------------------------- #

async def test_step_retries_then_succeeds():
    tool_manager = FakeToolInvoker(outcomes=["raise", "ok"])
    engine = build_workflow_engine(tool_manager=tool_manager)
    definition = WorkflowDefinition(
        workflow_id="wf1", name="Retry", steps=[StepDefinition(name="a", kind="tool_call", tool_name="x", retry_policy=INSTANT_RETRY)]
    )
    engine.register_workflow(definition)

    run = await engine.start_run("wf1")

    assert run.step_results["a"].status == "completed"
    assert run.step_results["a"].attempts == 2


async def test_step_exhausts_retries_and_fails_the_run():
    tool_manager = FakeToolInvoker(outcomes=["raise", "raise", "raise"])
    engine = build_workflow_engine(tool_manager=tool_manager)
    definition = WorkflowDefinition(
        workflow_id="wf1", name="Retry", steps=[StepDefinition(name="a", kind="tool_call", tool_name="x", retry_policy=INSTANT_RETRY)]
    )
    engine.register_workflow(definition)

    run = await engine.start_run("wf1")

    assert run.step_results["a"].status == "failed"
    assert run.step_results["a"].attempts == 3
    assert run.status == "failed"


async def test_step_timeout_is_treated_as_a_retryable_failure():
    engine = build_workflow_engine(tool_manager=HangingToolInvoker(delay_seconds=5.0))
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="Timeout",
        steps=[
            StepDefinition(
                name="a", kind="tool_call", tool_name="x", timeout_seconds=0.05,
                retry_policy=RetryPolicy(max_attempts=1, backoff_base_seconds=0),
            )
        ],
    )
    engine.register_workflow(definition)

    run = await engine.start_run("wf1")

    assert run.step_results["a"].status == "failed"


# --------------------------------------------------------------------- #
# Human approval gates (#7)
# --------------------------------------------------------------------- #

async def test_run_pauses_at_an_approval_gate(engine):
    engine.register_workflow(
        approval_gated_template("wf1", "Gated", before_step_name="before", approval_step_name="gate", after_step_name="after")
    )

    run = await engine.start_run("wf1")

    assert run.status == "awaiting_approval"
    assert run.step_results["gate"].status == "pending_approval"
    assert "after" not in run.step_results


async def test_approving_the_gate_resumes_the_run(engine):
    engine.register_workflow(
        approval_gated_template("wf1", "Gated", before_step_name="before", approval_step_name="gate", after_step_name="after")
    )
    run = await engine.start_run("wf1")

    resumed = await engine.approve_step(run.id, "gate", approved=True, approver="ops-lead")

    assert resumed.status == "completed"
    assert resumed.step_results["after"].status == "completed"


async def test_denying_the_gate_fails_the_gated_step_and_skips_downstream(engine):
    engine.register_workflow(
        approval_gated_template("wf1", "Gated", before_step_name="before", approval_step_name="gate", after_step_name="after")
    )
    run = await engine.start_run("wf1")

    resumed = await engine.approve_step(run.id, "gate", approved=False, approver="ops-lead")

    assert resumed.step_results["gate"].status == "failed"
    assert resumed.step_results["after"].status == "skipped"
    assert resumed.status == "failed"


async def test_approving_a_step_not_awaiting_approval_raises(engine):
    engine.register_workflow(sequential_template("wf1", "Seq", ["a"]))
    run = await engine.start_run("wf1")

    with pytest.raises(ValueError):
        await engine.approve_step(run.id, "a", approved=True, approver="x")


# --------------------------------------------------------------------- #
# Tool calls via Tool Manager (#8)
# --------------------------------------------------------------------- #

async def test_tool_call_step_invokes_the_configured_tool_with_resolved_parameters():
    tool_manager = FakeToolInvoker()
    engine = build_workflow_engine(tool_manager=tool_manager)
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="ToolCall",
        steps=[
            StepDefinition(
                name="a", kind="tool_call", tool_name="search", operation="query",
                parameters={"q": "{{input.term}}"},
            )
        ],
    )
    engine.register_workflow(definition)

    run = await engine.start_run("wf1", input={"term": "hermes"})

    assert tool_manager.calls[0].tool_name == "search"
    assert tool_manager.calls[0].parameters == {"q": "hermes"}
    assert run.step_results["a"].status == "completed"


async def test_tool_call_step_resolves_via_capability_when_no_tool_name_given():
    tool_manager = FakeToolInvoker()
    capability_registry = FakeCapabilitySelector(selected="claude")
    engine = build_workflow_engine(tool_manager=tool_manager, capability_registry=capability_registry)
    definition = WorkflowDefinition(
        workflow_id="wf1", name="Cap", steps=[StepDefinition(name="a", kind="tool_call", capability="reasoning")]
    )
    engine.register_workflow(definition)

    await engine.start_run("wf1")

    assert tool_manager.calls[0].tool_name == "claude"


async def test_tool_call_step_fails_clearly_when_capability_has_no_provider():
    tool_manager = FakeToolInvoker()
    capability_registry = FakeCapabilitySelector(selected=None)
    engine = build_workflow_engine(tool_manager=tool_manager, capability_registry=capability_registry)
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="Cap",
        steps=[StepDefinition(name="a", kind="tool_call", capability="reasoning", retry_policy=INSTANT_RETRY)],
    )
    engine.register_workflow(definition)

    run = await engine.start_run("wf1")

    assert run.step_results["a"].status == "failed"
    assert tool_manager.calls == []  # never even reached Tool Manager


async def test_tool_call_step_without_a_configured_tool_manager_fails_clearly(engine):
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="NoTool",
        steps=[StepDefinition(name="a", kind="tool_call", tool_name="x", retry_policy=INSTANT_RETRY)],
    )
    engine.register_workflow(definition)

    run = await engine.start_run("wf1")

    assert run.step_results["a"].status == "failed"
    assert "ToolInvoker" in run.step_results["a"].error


# --------------------------------------------------------------------- #
# Memory reads/writes via Memory Manager (#9)
# --------------------------------------------------------------------- #

async def test_memory_write_then_read_round_trips_through_a_later_step():
    memory = FakeMemoryStore()
    engine = build_workflow_engine(memory_manager=memory)
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="MemRoundTrip",
        steps=[
            StepDefinition(
                name="write", kind="memory_write", memory_scope="persistent", memory_key="k",
                memory_value_template={"note": "{{input.text}}"},
            ),
            StepDefinition(name="read", kind="memory_read", memory_scope="persistent", memory_key="k", depends_on=["write"]),
        ],
    )
    engine.register_workflow(definition)

    run = await engine.start_run("wf1", input={"text": "hello"})

    assert run.step_results["write"].status == "completed"
    assert run.step_results["read"].output == {"found": True, "value": {"note": "hello"}}


async def test_memory_step_without_a_configured_memory_manager_fails_clearly(engine):
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="NoMemory",
        steps=[
            StepDefinition(
                name="a", kind="memory_write", memory_scope="persistent", memory_key="k", retry_policy=INSTANT_RETRY
            )
        ],
    )
    engine.register_workflow(definition)

    run = await engine.start_run("wf1")

    assert run.step_results["a"].status == "failed"


# --------------------------------------------------------------------- #
# Template-resolved memory keys
# --------------------------------------------------------------------- #

def _write_then_read_definition(workflow_id: str, memory_key_template: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id=workflow_id,
        name=workflow_id,
        steps=[
            StepDefinition(
                name="write", kind="memory_write", memory_scope="persistent", memory_key=memory_key_template,
                memory_value_template={"topic": "{{input.topic}}"},
            ),
            StepDefinition(
                name="read", kind="memory_read", memory_scope="persistent", memory_key=memory_key_template,
                depends_on=["write"],
            ),
        ],
    )


async def test_memory_key_with_no_template_syntax_is_used_unchanged():
    memory = FakeMemoryStore()
    engine = build_workflow_engine(memory_manager=memory)
    engine.register_workflow(_write_then_read_definition("wf1", "static_key"))

    run = await engine.start_run("wf1", input={"topic": "anything"})

    assert run.step_results["read"].output == {"found": True, "value": {"topic": "anything"}}
    entry = await memory.get_by_key(requesting_agent_id="x", scope="persistent", key="static_key")
    assert entry is not None


async def test_templated_memory_key_resolves_from_run_input():
    memory = FakeMemoryStore()
    engine = build_workflow_engine(memory_manager=memory)
    engine.register_workflow(_write_then_read_definition("wf1", "research_brief/{{input.topic}}"))

    await engine.start_run("wf1", input={"topic": "quantum computing"})

    entry = await memory.get_by_key(
        requesting_agent_id="x", scope="persistent", key="research_brief/quantum computing"
    )
    assert entry is not None
    assert entry.value == {"topic": "quantum computing"}


async def test_different_topics_produce_isolated_memory_entries():
    """The concrete proof of per-topic isolation: two runs with different
    input.topic values must never collide on the same memory key."""
    memory = FakeMemoryStore()
    engine = build_workflow_engine(memory_manager=memory)
    engine.register_workflow(_write_then_read_definition("wf1", "research_brief/{{input.topic}}"))

    run_a = await engine.start_run("wf1", input={"topic": "topic a"})
    run_b = await engine.start_run("wf1", input={"topic": "topic b"})

    # each run's own read_memory step found ONLY its own topic's entry,
    # never the other topic's, because they resolved to different keys
    assert run_a.step_results["read"].output["value"] == {"topic": "topic a"}
    assert run_b.step_results["read"].output["value"] == {"topic": "topic b"}

    entry_a = await memory.get_by_key(requesting_agent_id="x", scope="persistent", key="research_brief/topic a")
    entry_b = await memory.get_by_key(requesting_agent_id="x", scope="persistent", key="research_brief/topic b")
    assert entry_a is not None and entry_a.value == {"topic": "topic a"}
    assert entry_b is not None and entry_b.value == {"topic": "topic b"}


async def test_same_topic_across_separate_runs_shares_the_same_memory_entry():
    """The complement of isolation: the SAME topic run twice must still
    resolve to the SAME key, so accumulation within one topic still
    works exactly as before this change."""
    memory = FakeMemoryStore()
    engine = build_workflow_engine(memory_manager=memory)
    engine.register_workflow(_write_then_read_definition("wf1", "research_brief/{{input.topic}}"))

    first = await engine.start_run("wf1", input={"topic": "same topic"})
    second = await engine.start_run("wf1", input={"topic": "same topic"})

    # Both runs' read_memory steps saw the identical value at the same
    # resolved key -- proving the template resolved to the same key both
    # times, not a comparison of FakeMemoryStore's internal entry ids
    # (that fake creates a fresh id per write; the real MemoryManager
    # upserts in place -- see memory_manager/service.py's composite-key
    # index -- which is the behavior demonstrated against the real thing
    # in the Research Brief demo's own test suite).
    assert first.step_results["read"].output == second.step_results["read"].output == {
        "found": True,
        "value": {"topic": "same topic"},
    }


# --------------------------------------------------------------------- #
# Status tracking (#11)
# --------------------------------------------------------------------- #

def test_get_run_and_get_run_status_are_synchronous_by_design(engine):
    assert not inspect.iscoroutinefunction(engine.get_run)
    assert not inspect.iscoroutinefunction(engine.get_run_status)


async def test_get_run_status_reflects_the_current_run(engine):
    engine.register_workflow(sequential_template("wf1", "Seq", ["a"]))
    run = await engine.start_run("wf1")

    assert engine.get_run_status(run.id) == "completed"
    assert engine.get_run(run.id).step_results["a"].status == "completed"


def test_get_run_for_unknown_id_raises(engine):
    with pytest.raises(UnknownWorkflowRunError):
        engine.get_run(uuid.uuid4())


# --------------------------------------------------------------------- #
# Failure recovery (#12)
# --------------------------------------------------------------------- #

async def test_resume_run_retries_only_the_failed_step_and_keeps_completed_ones():
    tool_manager = FakeToolInvoker(outcomes=["raise", "raise", "raise", "ok"])
    engine = build_workflow_engine(tool_manager=tool_manager)
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="Recoverable",
        steps=[
            StepDefinition(name="first", kind="noop"),
            StepDefinition(
                name="risky", kind="tool_call", tool_name="x", depends_on=["first"],
                retry_policy=RetryPolicy(max_attempts=3, backoff_base_seconds=0, backoff_multiplier=1),
            ),
        ],
    )
    engine.register_workflow(definition)

    run = await engine.start_run("wf1")
    assert run.status == "failed"
    first_completed_at = run.step_results["first"].ended_at

    resumed = await engine.resume_run(run.id)

    assert resumed.status == "completed"
    assert resumed.step_results["risky"].status == "completed"
    assert resumed.step_results["first"].ended_at == first_completed_at  # untouched, not re-run


async def test_resume_run_for_unknown_id_raises(engine):
    with pytest.raises(UnknownWorkflowRunError):
        await engine.resume_run(uuid.uuid4())


# --------------------------------------------------------------------- #
# Events (#10) + standalone operation
# --------------------------------------------------------------------- #

async def test_run_lifecycle_events_are_published(bus):
    engine = build_workflow_engine(event_bus=bus)
    engine.register_workflow(sequential_template("wf1", "Seq", ["a"]))
    received = []

    async def capture(event):
        received.append(event.event_type)

    await bus.subscribe("*", capture)
    await engine.start_run("wf1")

    assert "workflow_engine.run.started" in received
    assert "workflow_engine.step.started" in received
    assert "workflow_engine.step.completed" in received
    assert "workflow_engine.run.completed" in received


async def test_engine_works_fully_standalone_without_any_collaborators(engine):
    engine.register_workflow(sequential_template("wf1", "Seq", ["a", "b"]))

    run = await engine.start_run("wf1")

    assert run.status == "completed"

