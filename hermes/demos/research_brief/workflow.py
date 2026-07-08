"""The Research Brief Workflow definition.

A generic, reusable five-step workflow -- not business-specific. It's
parameterized entirely by its `input.topic` and uses only step kinds the
Workflow Engine already supports.

Two steps (`accept_topic`, `assemble_brief`) are `noop`-kind, and are
structural markers rather than data-producing actions: the Workflow
Engine's `noop` step always returns `{}` (see workflow_engine/service.py
-- unmodified here), so it has no output to carry forward. That's why
downstream steps reference `{{input.topic}}` directly rather than
`{{steps.accept_topic.output.topic}}`, and why "return a structured
brief" (step 5) is actually assembled by `runner.assemble_brief()` from
the run's step results, not produced by a DAG node. This is a deliberate
reflection of the real, unmodified engine's capabilities, not an
oversight.

Memory keys ARE template-resolved by the Workflow Engine (`memory_key`
goes through the same `{{input.<path>}}` templater as `parameters` and
`memory_value_template` -- see workflow_engine/service.py's
`_resolve_memory_key`, added specifically so this workflow could stop
sharing one fixed memory slot across every topic). `MEMORY_KEY_TEMPLATE`
below resolves to a distinct key per topic (e.g.
`research_brief/quantum computing`), so two different topics never
collide, while the SAME topic run twice still resolves to the SAME key
-- accumulation within a topic, isolation across topics. See
`hermes/modules/workflow_engine/tests/test_service.py`'s
`test_different_topics_produce_isolated_memory_entries` for the engine-
level proof, and this demo's own `tests/test_runner.py` for the
end-to-end proof.
"""
from __future__ import annotations

from hermes.core.supervisor.policy import RetryPolicy
from hermes.modules.workflow_engine.models import StepDefinition, WorkflowDefinition

RESEARCH_BRIEF_WORKFLOW_ID = "research_brief"
MOCK_RESEARCH_TOOL_NAME = "mock_research"
MEMORY_SCOPE = "persistent"
MEMORY_KEY_TEMPLATE = "research_brief/{{input.topic}}"


def build_research_brief_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id=RESEARCH_BRIEF_WORKFLOW_ID,
        name=RESEARCH_BRIEF_WORKFLOW_ID,
        description=(
            "Given a research topic: read prior notes, run a mocked research "
            "tool, save the result, and return a structured brief."
        ),
        steps=[
            # 1. Accept a research topic (structural marker -- see docstring)
            StepDefinition(name="accept_topic", kind="noop"),
            # 2. Read relevant memory
            StepDefinition(
                name="read_memory",
                kind="memory_read",
                depends_on=["accept_topic"],
                memory_scope=MEMORY_SCOPE,
                memory_key=MEMORY_KEY_TEMPLATE,
            ),
            # 3. Call a placeholder research tool via Tool Manager
            StepDefinition(
                name="call_research_tool",
                kind="tool_call",
                depends_on=["read_memory"],
                tool_name=MOCK_RESEARCH_TOOL_NAME,
                operation="research",
                parameters={"topic": "{{input.topic}}"},
                retry_policy=RetryPolicy(max_attempts=2, backoff_base_seconds=0.1, backoff_multiplier=2),
                timeout_seconds=10.0,
            ),
            # 4. Save the result to Memory
            StepDefinition(
                name="save_to_memory",
                kind="memory_write",
                depends_on=["call_research_tool"],
                memory_scope=MEMORY_SCOPE,
                memory_key=MEMORY_KEY_TEMPLATE,
                memory_value_template={
                    "topic": "{{input.topic}}",
                    "summary": "{{steps.call_research_tool.output.summary}}",
                    "sources": "{{steps.call_research_tool.output.sources}}",
                },
            ),
            # 5. Return a structured brief (assembled by runner.assemble_brief
            # from step results -- see docstring for why this is a noop here)
            StepDefinition(name="assemble_brief", kind="noop", depends_on=["save_to_memory"]),
        ],
    )
