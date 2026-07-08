"""Narrow Protocols for the Mission System's collaborators.

Same "depend on the shape you use, not the concrete class" pattern used
throughout this codebase (Commander's contracts.py, Workflow Engine's
contracts.py). None of these are new capabilities -- they're subsets of
Commander's, the Intent Router's, and Memory Manager's already-built,
already-tested public interfaces.
"""
from __future__ import annotations

from typing import Protocol

from hermes.core.commander.models import IncomingRequest, Intent, StructuredResponse, WorkflowPlan


class RequestHandler(Protocol):
    """What Mission System needs from Commander: just `handle_request`.
    Executing a mission's required workflows is entirely delegated to
    this -- Mission System dispatches no task itself."""

    async def handle_request(self, request: IncomingRequest) -> StructuredResponse: ...


class IntentResolver(Protocol):
    """What Mission System needs from the Intent Router: `classify` +
    `resolve`, used to infer a workflow from the mission's goal text when
    `required_workflows` wasn't given explicitly."""

    async def classify(self, request: IncomingRequest) -> Intent: ...
    async def resolve(self, intent: Intent, request: IncomingRequest) -> WorkflowPlan: ...


class MemoryPermissionGranter(Protocol):
    """What the Team Builder needs from Memory Manager: `grant_permission`/
    `revoke_permission` -- both sync methods on the real `MemoryManager`
    (pure bookkeeping, no event bus involved), consistent with its own
    design."""

    def grant_permission(
        self, agent_id: str, *, owner_agent_id: str | None = None, can_read: bool = True, can_write: bool = False
    ) -> None: ...

    def revoke_permission(self, agent_id: str, *, owner_agent_id: str | None = None) -> None: ...
