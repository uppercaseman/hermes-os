import pytest

from hermes.core.commander.models import (
    AgentRequirement,
    ApprovalDecision,
    Intent,
    MemoryRequirement,
    ToolRequirement,
    WorkflowPlan,
)
from hermes.core.commander.tests.fakes import (
    FakeAgentResolver,
    FakeApprovalPolicy,
    FakeIntentClassifier,
    FakeMemoryResolver,
    FakeToolResolver,
    FakeWorkflowResolver,
    RecordingTaskDispatcher,
)
from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.core.supervisor.policy import RetryPolicy


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def sample_intent() -> Intent:
    return Intent(name="answer_question", confidence=0.9)


@pytest.fixture
def sample_workflow() -> WorkflowPlan:
    return WorkflowPlan(workflow_id="wf-1", name="simple_qa", steps=["retrieve", "respond"])


@pytest.fixture
def sample_agents() -> list[AgentRequirement]:
    return [AgentRequirement(agent_name="researcher", role="primary")]


@pytest.fixture
def sample_tools() -> list[ToolRequirement]:
    return [ToolRequirement(tool_name="web_search", reason="needs fresh info")]


@pytest.fixture
def sample_memory() -> MemoryRequirement:
    return MemoryRequirement(scope="session", keys=["conversation"])


@pytest.fixture
def no_approval_needed() -> ApprovalDecision:
    return ApprovalDecision(required=False)


@pytest.fixture
def dispatcher() -> RecordingTaskDispatcher:
    return RecordingTaskDispatcher()


@pytest.fixture
def commander_kwargs(
    bus,
    sample_intent,
    sample_workflow,
    sample_agents,
    sample_tools,
    sample_memory,
    no_approval_needed,
    dispatcher,
) -> dict:
    """Base kwargs for build_commander(); tests override individual
    collaborators (e.g. task_dispatcher, approval_policy) as needed."""
    return {
        "event_bus": bus,
        "intent_classifier": FakeIntentClassifier(sample_intent),
        "workflow_resolver": FakeWorkflowResolver(sample_workflow),
        "agent_resolver": FakeAgentResolver(sample_agents),
        "tool_resolver": FakeToolResolver(sample_tools),
        "memory_resolver": FakeMemoryResolver(sample_memory),
        "approval_policy": FakeApprovalPolicy(no_approval_needed),
        "task_dispatcher": dispatcher,
        "retry_policy": RetryPolicy(max_attempts=3, backoff_base_seconds=0, backoff_multiplier=1),
        "task_timeout_seconds": 2.0,
    }
