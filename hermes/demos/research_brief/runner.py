"""Wires Commander, Workflow Engine, Tool Manager, Memory Manager, and
the Event Bus into one real, running pipeline for the Research Brief
vertical slice.

One subtlety this file exists to solve: Commander's `Plan.build_tasks()`
(core/commander/models.py, unmodified) only serializes `workflow_id`,
`agents`, `tools`, and `memory` into a dispatched task's payload -- it
never carries the original free-text request through. So by the time
`WorkflowEngineTaskDispatcher` (workflow_engine/commander_bridge.py,
also unmodified) receives the task, the research topic the user typed
is nowhere in it.

The fix doesn't touch either of those two already-built, already-tested
files. `IncomingRequest.correlation_id` is set explicitly by this
runner and is guaranteed to survive, unchanged, all the way from the
request through `Plan.correlation_id` to `DispatchedTask.correlation_id`
(Commander's own code already does this). So: a small in-process
registry maps that correlation_id to the original topic, populated by a
demo-specific wrapper around the real `IntentRouter` (the first
collaborator to see the raw request text) and consumed by a demo-specific
dispatcher that WRAPS the real `WorkflowEngineTaskDispatcher` --
injecting the topic into the task's payload immediately before
delegating to it, rather than reimplementing its dispatch/report logic.

Intent routing itself is no longer fixed: `build_research_brief_pipeline`
wires a real `IntentRouter` (modules/intent_router) with one registered
route rather than a resolver that always returns the same workflow. The
CLI still always reaches the Research Brief workflow for any topic text
-- not because the router can't discriminate, but because
`run_research_brief` sets `metadata={"intent": RESEARCH_BRIEF_WORKFLOW_ID}`
on the request, which is the router's explicit-intent-hint match (the
correct way for a purpose-built, single-workflow CLI to route, since
free-form research topics can't be relied on to contain any particular
keyword). The router's keyword/command matching -- and its genuine
"no match, no default -> failure" behavior -- are exercised directly in
`tests/test_runner.py` by calling Commander without that metadata.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from hermes.core.commander.interface import Commander, StructuredResponse, build_commander
from hermes.core.commander.models import (
    AgentRequirement,
    ApprovalDecision,
    DispatchedTask,
    IncomingRequest,
    Intent,
    MemoryRequirement,
    ToolRequirement,
    WorkflowPlan,
)
from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.demos.research_brief.mock_research_adapter import MockResearchAdapter
from hermes.demos.research_brief.workflow import (
    MOCK_RESEARCH_TOOL_NAME,
    RESEARCH_BRIEF_WORKFLOW_ID,
    build_research_brief_workflow,
)
from hermes.modules.intent_router.interface import IntentRouter, WorkflowRoute, build_intent_router
from hermes.modules.memory_manager.interface import MemoryManager, build_memory_manager
from hermes.modules.tool_manager.interface import ToolManager, build_tool_manager
from hermes.modules.tool_manager.models import ToolAdapterConfig
from hermes.modules.workflow_engine.commander_bridge import WorkflowEngineTaskDispatcher
from hermes.modules.workflow_engine.interface import WorkflowEngine, build_workflow_engine

_TASK_DISPATCH_TIMEOUT_SECONDS = 15.0


# --------------------------------------------------------------------- #
# Minimal demo-only Commander collaborators.
#
# Intent routing (research_brief vs. anything else) is now real --
# handled by the generic IntentRouter, not hardcoded here. What remains
# demo-specific and deliberately minimal: agent/tool/memory planning
# bookkeeping this single-workflow demo doesn't need, and the
# topic-carrying glue described in the module docstring.
# --------------------------------------------------------------------- #
class _TopicCapturingIntentClassifier:
    """Wraps a real `IntentRouter`, additionally stashing the raw
    request text (keyed by correlation_id) for the dispatcher to find
    later -- see the module docstring for why."""

    def __init__(self, inner: IntentRouter, topic_registry: dict[uuid.UUID, str]) -> None:
        self._inner = inner
        self._topic_registry = topic_registry

    async def classify(self, request: IncomingRequest) -> Intent:
        if request.correlation_id is not None:
            self._topic_registry[request.correlation_id] = request.raw_input
        return await self._inner.classify(request)


class _NoAgentsResolver:
    async def resolve(self, intent: Intent, workflow: WorkflowPlan) -> list[AgentRequirement]:
        return []


class _NoToolsResolver:
    """Commander's OWN tool bookkeeping is unused here -- the actual
    tool call happens inside the Workflow Engine, via its own
    `tool_manager` collaborator, not through Commander's plan."""

    async def resolve(self, intent: Intent, workflow: WorkflowPlan) -> list[ToolRequirement]:
        return []


class _FixedMemoryResolver:
    async def resolve(self, intent: Intent, workflow: WorkflowPlan) -> MemoryRequirement:
        return MemoryRequirement(scope="persistent", keys=[])


class _NoApprovalPolicy:
    async def evaluate(self, plan) -> ApprovalDecision:
        return ApprovalDecision(required=False)


class _TopicInjectingDispatcher:
    """Wraps a real `WorkflowEngineTaskDispatcher`, injecting the
    original topic into the task payload (looked up by correlation_id)
    immediately before delegating -- reuses the real bridge's
    dispatch/report logic rather than duplicating it."""

    def __init__(self, *, inner: WorkflowEngineTaskDispatcher, topic_registry: dict[uuid.UUID, str]) -> None:
        self._inner = inner
        self._topic_registry = topic_registry

    async def dispatch(self, task: DispatchedTask) -> None:
        task.payload["topic"] = self._topic_registry.pop(task.correlation_id, "")
        await self._inner.dispatch(task)


# --------------------------------------------------------------------- #
# Pipeline construction
# --------------------------------------------------------------------- #
@dataclass
class ResearchBriefPipeline:
    commander: Commander
    engine: WorkflowEngine
    tool_manager: ToolManager
    memory_manager: MemoryManager
    event_bus: InMemoryEventBus


def build_research_brief_pipeline() -> ResearchBriefPipeline:
    """Wires one full, real Hermes pipeline for the Research Brief demo:
    Commander -> Workflow Engine -> Tool Manager (with the mock research
    adapter registered) -> Memory Manager, all sharing one Event Bus."""
    bus = InMemoryEventBus()

    memory_manager = build_memory_manager(event_bus=bus)

    tool_manager = build_tool_manager(event_bus=bus)
    tool_manager.register_adapter(
        MockResearchAdapter(name=MOCK_RESEARCH_TOOL_NAME), ToolAdapterConfig(name=MOCK_RESEARCH_TOOL_NAME)
    )

    engine = build_workflow_engine(event_bus=bus, tool_manager=tool_manager, memory_manager=memory_manager)
    engine.register_workflow(build_research_brief_workflow())

    router = build_intent_router()
    router.add_route(
        WorkflowRoute(
            workflow_id=RESEARCH_BRIEF_WORKFLOW_ID,
            intent_names=[RESEARCH_BRIEF_WORKFLOW_ID],
            keywords=["research", "investigate", "brief"],
            command="/research",
        )
    )

    topic_registry: dict[uuid.UUID, str] = {}
    bridge = WorkflowEngineTaskDispatcher(engine=engine, event_bus=bus)
    dispatcher = _TopicInjectingDispatcher(inner=bridge, topic_registry=topic_registry)

    commander = build_commander(
        event_bus=bus,
        intent_classifier=_TopicCapturingIntentClassifier(router, topic_registry),
        workflow_resolver=router,  # the SAME IntentRouter instance satisfies both protocols
        agent_resolver=_NoAgentsResolver(),
        tool_resolver=_NoToolsResolver(),
        memory_resolver=_FixedMemoryResolver(),
        approval_policy=_NoApprovalPolicy(),
        task_dispatcher=dispatcher,
        task_timeout_seconds=_TASK_DISPATCH_TIMEOUT_SECONDS,
    )

    return ResearchBriefPipeline(
        commander=commander, engine=engine, tool_manager=tool_manager, memory_manager=memory_manager, event_bus=bus
    )


# --------------------------------------------------------------------- #
# Running the demo + assembling the structured brief
# --------------------------------------------------------------------- #
async def run_research_brief(
    topic: str, *, pipeline: ResearchBriefPipeline | None = None, requester: str = "cli-demo"
) -> dict[str, Any]:
    """Runs the full vertical slice for one topic and returns a
    structured brief. Pass an existing `pipeline` to run multiple topics
    against the same Memory Manager (so a later run's `read_memory` step
    can see an earlier run's `save_to_memory` result); omit it to build a
    fresh, isolated pipeline -- what the CLI does for a one-shot
    invocation. Sets `metadata={"intent": ...}` explicitly so the router
    always reaches the Research Brief workflow regardless of the topic's
    wording -- see the module docstring for why that's the correct way
    for a purpose-built, single-workflow CLI to route, rather than
    relying on keyword matching against free-form research topics."""
    pipeline = pipeline or build_research_brief_pipeline()
    request = IncomingRequest(
        raw_input=topic,
        requester=requester,
        correlation_id=uuid.uuid4(),
        metadata={"intent": RESEARCH_BRIEF_WORKFLOW_ID},
    )
    response = await pipeline.commander.handle_request(request)
    return assemble_brief(response, pipeline.engine)


def assemble_brief(response: StructuredResponse, engine: WorkflowEngine) -> dict[str, Any]:
    """Builds the "structured brief" (step 5) from the workflow run's
    step results. This is deliberately done here, not inside the
    workflow's own DAG -- see workflow.py's docstring for why a `noop`
    step can't carry this data forward itself."""
    if response.status != "completed" or not response.task_results:
        return {"status": response.status, "summary": response.summary, "brief": None}

    task_output = response.task_results[0].output or {}
    run_id = task_output.get("run_id")
    if run_id is None:
        return {"status": "failed", "summary": "workflow did not report a run id", "brief": None}

    run = engine.get_run(uuid.UUID(run_id))
    tool_result = run.step_results.get("call_research_tool")
    memory_result = run.step_results.get("save_to_memory")
    tool_output = tool_result.output if tool_result and tool_result.output else {}
    memory_output = memory_result.output if memory_result and memory_result.output else {}

    return {
        "status": run.status,
        "topic": run.input.get("topic"),
        "summary": tool_output.get("summary"),
        "sources": tool_output.get("sources"),
        "memory_entry_id": memory_output.get("entry_id"),
        "step_statuses": {name: result.status for name, result in run.step_results.items()},
    }
