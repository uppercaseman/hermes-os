"""Workflow Engine-specific exception types."""
from __future__ import annotations

import uuid


class InvalidWorkflowDefinitionError(Exception):
    """Raised at `register_workflow` time -- a malformed definition never
    reaches run time."""

    def __init__(self, workflow_id: str, reason: str) -> None:
        self.workflow_id = workflow_id
        self.reason = reason
        super().__init__(f"invalid workflow definition {workflow_id!r}: {reason}")


class UnknownWorkflowError(Exception):
    def __init__(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id
        super().__init__(f"no workflow registered with id {workflow_id!r}")


class UnknownWorkflowRunError(Exception):
    def __init__(self, run_id: uuid.UUID) -> None:
        self.run_id = run_id
        super().__init__(f"no workflow run with id {run_id}")


class WorkflowEngineConfigError(Exception):
    """Raised when a step needs a collaborator (ToolInvoker, MemoryStore,
    CapabilitySelector) that wasn't configured on this engine."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
