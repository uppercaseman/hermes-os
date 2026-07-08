"""Public entry point for the Workflow Engine.

Everything outside this package imports from here, never from
service.py directly -- mirrors every other module's interface.py
convention.
"""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.modules.workflow_engine.contracts import CapabilitySelector, MemoryStore, ToolInvoker
from hermes.modules.workflow_engine.errors import (
    InvalidWorkflowDefinitionError,
    UnknownWorkflowError,
    UnknownWorkflowRunError,
    WorkflowEngineConfigError,
)
from hermes.modules.workflow_engine.models import (
    StepCondition,
    StepDefinition,
    StepResult,
    WorkflowDefinition,
    WorkflowRun,
)
from hermes.modules.workflow_engine.service import WorkflowEngine

__all__ = [
    "WorkflowEngine",
    "WorkflowDefinition",
    "StepDefinition",
    "StepCondition",
    "StepResult",
    "WorkflowRun",
    "ToolInvoker",
    "MemoryStore",
    "CapabilitySelector",
    "InvalidWorkflowDefinitionError",
    "UnknownWorkflowError",
    "UnknownWorkflowRunError",
    "WorkflowEngineConfigError",
    "build_workflow_engine",
]


def build_workflow_engine(
    *,
    event_bus: EventBus | None = None,
    tool_manager: ToolInvoker | None = None,
    memory_manager: MemoryStore | None = None,
    capability_registry: CapabilitySelector | None = None,
) -> WorkflowEngine:
    return WorkflowEngine(
        event_bus=event_bus,
        tool_manager=tool_manager,
        memory_manager=memory_manager,
        capability_registry=capability_registry,
    )
