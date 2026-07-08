"""Public entry point for Hermes Commander.

Everything outside this module -- CLI, HTTP API, tests -- imports from
here, never from service.py directly. This mirrors the OS-wide rule that a
module's internals are private; only its interface is a stable contract.
"""
from __future__ import annotations

from hermes.core.commander.contracts import (
    AgentResolver,
    ApprovalPolicy,
    IntentClassifier,
    MemoryResolver,
    TaskDispatcher,
    ToolResolver,
    WorkflowResolver,
)
from hermes.core.commander.models import (
    ApprovalDecision,
    IncomingRequest,
    Plan,
    StructuredResponse,
)
from hermes.core.commander.service import Commander
from hermes.core.event_bus.interface import EventBus
from hermes.core.supervisor.policy import RetryPolicy

__all__ = [
    "Commander",
    "IncomingRequest",
    "StructuredResponse",
    "Plan",
    "ApprovalDecision",
    "build_commander",
]


def build_commander(
    *,
    event_bus: EventBus,
    intent_classifier: IntentClassifier,
    workflow_resolver: WorkflowResolver,
    agent_resolver: AgentResolver,
    tool_resolver: ToolResolver,
    memory_resolver: MemoryResolver,
    approval_policy: ApprovalPolicy,
    task_dispatcher: TaskDispatcher,
    retry_policy: RetryPolicy | None = None,
    task_timeout_seconds: float = 30.0,
    planning_timeout_seconds: float = 30.0,
) -> Commander:
    """Wires a Commander instance from its collaborators.

    Every collaborator is a Protocol from contracts.py -- callers pass in
    whatever concrete module implementation (or test fake) satisfies it.
    Commander itself never constructs a collaborator; it only consumes one.

    `planning_timeout_seconds` bounds each individual planning-phase call
    (intent classification, workflow/agent/tool/memory resolution) -- it
    is a new, optional parameter with a default, so existing callers that
    don't pass it are unaffected.
    """
    return Commander(
        event_bus=event_bus,
        intent_classifier=intent_classifier,
        workflow_resolver=workflow_resolver,
        agent_resolver=agent_resolver,
        tool_resolver=tool_resolver,
        memory_resolver=memory_resolver,
        approval_policy=approval_policy,
        task_dispatcher=task_dispatcher,
        retry_policy=retry_policy,
        task_timeout_seconds=task_timeout_seconds,
        planning_timeout_seconds=planning_timeout_seconds,
    )
