"""Test doubles satisfying the Mission System's narrow collaborator
Protocols -- not real Commander/Intent Router implementations, used
only to exercise MissionSystem's own orchestration logic in isolation.
"""
from __future__ import annotations

import uuid

from hermes.core.commander.models import IncomingRequest, Intent, StructuredResponse, WorkflowPlan


class FakeCommander:
    """Scripts a sequence of outcomes ("completed"/"failed") consumed in
    the order `handle_request` is called, one per call."""

    def __init__(self, outcomes: list[str] | None = None) -> None:
        self._outcomes = list(outcomes) if outcomes is not None else ["completed"]
        self.requests: list[IncomingRequest] = []

    async def handle_request(self, request: IncomingRequest) -> StructuredResponse:
        self.requests.append(request)
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        correlation_id = request.correlation_id or uuid.uuid4()
        return StructuredResponse(
            request_id=request.id,
            correlation_id=correlation_id,
            status=outcome,
            plan=None,
            task_results=[],
            summary=f"scripted {outcome}",
        )


class FakeIntentRouter:
    def __init__(self, *, workflow_id: str | None = None, raise_unknown: bool = False) -> None:
        self._workflow_id = workflow_id
        self._raise_unknown = raise_unknown

    async def classify(self, request: IncomingRequest) -> Intent:
        if self._raise_unknown:
            return Intent(name="unknown", confidence=0.0)
        return Intent(name=self._workflow_id, confidence=1.0)

    async def resolve(self, intent: Intent, request: IncomingRequest) -> WorkflowPlan:
        if self._raise_unknown or intent.name == "unknown":
            from hermes.modules.intent_router.errors import UnknownIntentError

            raise UnknownIntentError(request.raw_input)
        return WorkflowPlan(workflow_id=intent.name, name=intent.name, steps=[])


class FakeMemoryPermissionGranter:
    """Records grant/revoke calls instead of touching a real Memory
    Manager -- used to test TeamBuilder's permission-granting behavior
    in isolation."""

    def __init__(self) -> None:
        self.grants: list[tuple[str, str | None, bool, bool]] = []
        self.revocations: list[tuple[str, str | None]] = []

    def grant_permission(
        self, agent_id: str, *, owner_agent_id: str | None = None, can_read: bool = True, can_write: bool = False
    ) -> None:
        self.grants.append((agent_id, owner_agent_id, can_read, can_write))

    def revoke_permission(self, agent_id: str, *, owner_agent_id: str | None = None) -> None:
        self.revocations.append((agent_id, owner_agent_id))
