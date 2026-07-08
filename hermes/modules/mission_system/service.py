"""Mission System -- converts a user goal into an executable mission.

Sits one layer above Commander: Commander answers "handle this one
request"; a Mission answers "accomplish this goal", which may run one or
more workflows (via Commander, never dispatched directly) over its
lifetime, with its own success criteria, a temporary team, and an
approval gate that is a DIFFERENT scope from Commander's plan-level gate
and Workflow Engine's step-level gate -- see README.md for why these
three don't duplicate each other.

No specialist agents, no AI calls: `TeamBuilder` assigns temporary,
permission-scoped roles; all actual execution is delegated entirely to
the already-built Commander -> Workflow Engine pipeline.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from hermes.core.commander.models import IncomingRequest
from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.mission_system import events as evt
from hermes.modules.mission_system.contracts import IntentResolver, RequestHandler
from hermes.modules.mission_system.errors import (
    MissionNotReadyError,
    MissionSystemConfigError,
    UnknownApprovalGateError,
    UnknownMissionError,
)
from hermes.modules.mission_system.models import ApprovalRecord, Mission, SuccessCriterion
from hermes.modules.mission_system.team_builder import TeamBuilder

SOURCE_MODULE = "mission_system"

_READY_TO_EXECUTE_STATUSES = {"team_assigned", "awaiting_approval"}


class MissionSystem:
    def __init__(
        self,
        *,
        commander: RequestHandler | None = None,
        intent_router: IntentResolver | None = None,
        event_bus: EventBus | None = None,
        team_builder: TeamBuilder | None = None,
    ) -> None:
        self._commander = commander
        self._intent_router = intent_router
        self._bus = event_bus
        self._team_builder = team_builder or TeamBuilder()
        self._missions: dict[uuid.UUID, Mission] = {}

    # ------------------------------------------------------------------ #
    # Mission lifecycle
    # ------------------------------------------------------------------ #
    async def create_mission(
        self,
        *,
        goal: str,
        success_criteria: list[str] | None = None,
        required_capabilities: list[str] | None = None,
        required_tools: list[str] | None = None,
        required_memory_scopes: list[str] | None = None,
        required_workflows: list[str] | None = None,
        required_approvals: list[str] | None = None,
        requested_roles: list[str] | None = None,
    ) -> Mission:
        mission = Mission(
            goal=goal,
            success_criteria=[SuccessCriterion(description=c) for c in (success_criteria or [])],
            required_capabilities=required_capabilities or [],
            required_tools=required_tools or [],
            required_memory_scopes=required_memory_scopes or [],
            required_workflows=required_workflows or [],
            required_approvals=required_approvals or [],
            requested_roles=requested_roles or [],
        )
        self._missions[mission.id] = mission
        await self._publish(evt.MISSION_CREATED, mission, {})
        return mission

    async def assign_team(self, mission_id: uuid.UUID) -> Mission:
        mission = self._require(mission_id)
        mission.assigned_team = self._team_builder.build_team(mission)
        mission.status = "team_assigned"
        mission.updated_at = datetime.now(timezone.utc)
        await self._publish(evt.TEAM_ASSIGNED, mission, {"roles": [r.role_name for r in mission.assigned_team]})
        return mission

    async def approve(self, mission_id: uuid.UUID, gate_name: str, *, approved: bool, approver: str) -> Mission:
        mission = self._require(mission_id)
        if gate_name not in mission.required_approvals:
            raise UnknownApprovalGateError(mission_id, gate_name)
        mission.approvals_granted[gate_name] = ApprovalRecord(
            gate_name=gate_name, approved=approved, approver=approver, decided_at=datetime.now(timezone.utc)
        )
        mission.updated_at = datetime.now(timezone.utc)
        await self._publish(evt.APPROVAL_DECIDED, mission, {"gate": gate_name, "approved": approved, "approver": approver})
        return mission

    async def execute_mission(self, mission_id: uuid.UUID) -> Mission:
        """Runs every required workflow, in order, via Commander --
        never dispatches directly. Stops at the first workflow that
        doesn't complete. Never raises for a workflow's own failure or
        for a missing IntentRouter needed to infer one (both become
        `status="failed"`); DOES raise for genuine preconditions checked
        before any execution is attempted: no team ever assigned, or no
        Commander configured at all.
        """
        mission = self._require(mission_id)
        if mission.status not in _READY_TO_EXECUTE_STATUSES:
            raise MissionNotReadyError(mission_id, mission.status)

        if mission.required_approvals and not self._all_approved(mission):
            mission.status = "awaiting_approval"
            mission.updated_at = datetime.now(timezone.utc)
            await self._publish(evt.MISSION_AWAITING_APPROVAL, mission, {"pending": self._pending_gates(mission)})
            return mission

        if self._commander is None:
            raise MissionSystemConfigError("execute_mission requires a configured Commander")

        try:
            if not mission.required_workflows:
                await self._infer_required_workflow(mission)
        except Exception as exc:  # noqa: BLE001 -- an unroutable goal fails the mission, never raises past this boundary
            mission.status = "failed"
            mission.updated_at = datetime.now(timezone.utc)
            await self._publish(evt.MISSION_FAILED, mission, {"error": str(exc)})
            return mission

        mission.status = "active"
        await self._publish(evt.MISSION_STARTED, mission, {})

        for workflow_id in mission.required_workflows:
            request = IncomingRequest(
                raw_input=mission.goal,
                requester="mission_system",
                # Deliberately mission.id, not a fresh uuid4(): this is
                # the ONLY channel that carries a mission's identity
                # through Commander's Plan into a DispatchedTask (which
                # has no mission_id field of its own) -- Task Queue's
                # Commander bridge relies on exactly this convention for
                # mission-level task tracking. See
                # task_queue/commander_dispatcher.py.
                correlation_id=mission.id,
                metadata={"intent": workflow_id},
            )
            response = await self._commander.handle_request(request)
            mission.outputs[workflow_id] = response.model_dump(mode="json")
            if response.status != "completed":
                mission.status = "failed"
                mission.updated_at = datetime.now(timezone.utc)
                await self._publish(evt.MISSION_FAILED, mission, {"workflow_id": workflow_id})
                return mission

        mission.status = "completed"
        mission.updated_at = datetime.now(timezone.utc)
        await self._publish(evt.MISSION_COMPLETED, mission, {})
        return mission

    async def dissolve_mission(self, mission_id: uuid.UUID) -> Mission:
        """The mission's final step: dissolves the temporary team via
        `TeamBuilder.dissolve_team`. Safe to call from any status,
        including `failed` or `draft` (a mission can be abandoned
        early)."""
        mission = self._require(mission_id)
        self._team_builder.dissolve_team(mission)
        mission.status = "dissolved"
        mission.updated_at = datetime.now(timezone.utc)
        await self._publish(evt.MISSION_DISSOLVED, mission, {})
        return mission

    # ------------------------------------------------------------------ #
    # Success criteria bookkeeping (never evaluated automatically)
    # ------------------------------------------------------------------ #
    def mark_success_criterion(self, mission_id: uuid.UUID, description: str, *, met: bool) -> Mission:
        mission = self._require(mission_id)
        for criterion in mission.success_criteria:
            if criterion.description == description:
                criterion.met = met
                return mission
        raise ValueError(f"mission {mission_id} has no success criterion {description!r}")

    # ------------------------------------------------------------------ #
    # Queries -- synchronous by design, same rationale as State Manager /
    # Workflow Engine: a pure in-memory read must never be blocked.
    # ------------------------------------------------------------------ #
    def get_mission(self, mission_id: uuid.UUID) -> Mission:
        return self._require(mission_id)

    def get_mission_status(self, mission_id: uuid.UUID) -> str:
        return self._require(mission_id).status

    def list_missions(self) -> list[Mission]:
        return list(self._missions.values())

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    async def _infer_required_workflow(self, mission: Mission) -> None:
        if self._intent_router is None:
            raise MissionSystemConfigError(
                "mission has no required_workflows and no IntentRouter is configured to infer one"
            )
        request = IncomingRequest(raw_input=mission.goal, requester="mission_system")
        intent = await self._intent_router.classify(request)
        plan = await self._intent_router.resolve(intent, request)  # may raise UnknownIntentError -- caught by the caller
        mission.required_workflows = [plan.workflow_id]

    def _all_approved(self, mission: Mission) -> bool:
        return all(
            mission.approvals_granted.get(gate, ApprovalRecord(gate_name=gate)).approved
            for gate in mission.required_approvals
        )

    def _pending_gates(self, mission: Mission) -> list[str]:
        return [
            gate
            for gate in mission.required_approvals
            if not mission.approvals_granted.get(gate, ApprovalRecord(gate_name=gate)).approved
        ]

    def _require(self, mission_id: uuid.UUID) -> Mission:
        if mission_id not in self._missions:
            raise UnknownMissionError(mission_id)
        return self._missions[mission_id]

    async def _publish(self, event_type: str, mission: Mission, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module=SOURCE_MODULE,
                correlation_id=mission.id,
                payload={"mission_id": str(mission.id), "status": mission.status, **payload},
            )
        )
