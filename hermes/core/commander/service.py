"""Hermes Commander -- the OS kernel's orchestrator.

Commander is the single entry point for every request into the system. It
does none of the real work itself: it determines intent, assembles a plan
by asking its collaborator modules what's required, checks whether the
plan needs human approval, dispatches the resulting tasks to the Task
Queue, and watches them through to completion -- retrying failures per
policy -- before handing back one structured response.

It contains no specialist-agent logic and no business rules of its own;
every decision is delegated to a collaborator through the Protocol
contracts in contracts.py. Every state transition is published to the
event bus before or as it happens, which is what makes a run replayable
from the log alone.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Awaitable, TypeVar

from hermes.core.commander import events as evt
from hermes.core.commander.contracts import (
    AgentResolver,
    ApprovalPolicy,
    IntentClassifier,
    MemoryResolver,
    TaskDispatcher,
    ToolResolver,
    WorkflowResolver,
)
from hermes.core.commander.errors import PlanningTimeoutError
from hermes.core.commander.models import (
    ApprovalDecision,
    DispatchedTask,
    IncomingRequest,
    Plan,
    StructuredResponse,
    TaskResult,
)
from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.core.supervisor.policy import RetryPolicy

logger = logging.getLogger(__name__)

SOURCE_MODULE = "commander"

_T = TypeVar("_T")


class Commander:
    """The OS kernel's orchestrator. See the module docstring above for
    what it does and does not own.

    Constructing a Commander only wires it to its collaborators -- nothing
    runs until `handle_request` (or `resume_after_approval`) is called.
    """

    def __init__(
        self,
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
    ) -> None:
        self._bus = event_bus
        self._intent_classifier = intent_classifier
        self._workflow_resolver = workflow_resolver
        self._agent_resolver = agent_resolver
        self._tool_resolver = tool_resolver
        self._memory_resolver = memory_resolver
        self._approval_policy = approval_policy
        self._dispatcher = task_dispatcher
        self._retry_policy = retry_policy or RetryPolicy()
        self._task_timeout_seconds = task_timeout_seconds
        self._planning_timeout_seconds = planning_timeout_seconds

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    async def handle_request(self, request: IncomingRequest) -> StructuredResponse:
        """Receive, plan, approve-gate, dispatch, monitor -- and always
        return a StructuredResponse. Never raises for a collaborator's
        failure; that becomes a `status="failed"` response instead."""
        correlation_id = request.correlation_id or uuid.uuid4()
        await self._publish(evt.REQUEST_RECEIVED, correlation_id, {"request_id": str(request.id)})

        try:
            plan = await self._build_plan(request, correlation_id)
        except Exception as exc:  # noqa: BLE001 -- a collaborator's bug must
            # never crash Commander or escape as a raw exception; it becomes
            # a structured failure response and a logged event instead.
            logger.exception("planning failed for request_id=%s", request.id)
            await self._publish(evt.RUN_FAILED, correlation_id, {"stage": "planning", "error": str(exc)})
            return StructuredResponse(
                request_id=request.id,
                correlation_id=correlation_id,
                status="failed",
                plan=None,
                task_results=[],
                summary=f"Planning failed: {exc}",
            )

        approval = await self._approval_policy.evaluate(plan)
        plan.approval = approval

        if approval.required and not approval.approved:
            await self._publish(evt.APPROVAL_REQUIRED, correlation_id, approval.model_dump())
            return StructuredResponse(
                request_id=request.id,
                correlation_id=correlation_id,
                status="awaiting_approval",
                plan=plan,
                task_results=[],
                summary="Plan requires human approval before dispatch.",
            )

        await self._publish(
            evt.APPROVAL_GRANTED if approval.required else evt.APPROVAL_NOT_REQUIRED,
            correlation_id,
            approval.model_dump(),
        )
        return await self._dispatch_plan(request.id, plan)

    async def resume_after_approval(
        self, request_id: uuid.UUID, plan: Plan, approval: ApprovalDecision
    ) -> StructuredResponse:
        """Continues a plan previously returned as `awaiting_approval`, once
        a human has made a decision out of band (e.g. via `hermes approval
        grant <request_id>`)."""
        plan.approval = approval
        if not approval.approved:
            await self._publish(evt.APPROVAL_DENIED, plan.correlation_id, approval.model_dump())
            return StructuredResponse(
                request_id=request_id,
                correlation_id=plan.correlation_id,
                status="failed",
                plan=plan,
                task_results=[],
                summary=f"Approval denied: {approval.reason or 'no reason given'}",
            )
        await self._publish(evt.APPROVAL_GRANTED, plan.correlation_id, approval.model_dump())
        return await self._dispatch_plan(request_id, plan)

    # ------------------------------------------------------------------ #
    # Planning: intent -> workflow -> agents -> tools -> memory
    # ------------------------------------------------------------------ #
    async def _build_plan(self, request: IncomingRequest, correlation_id: uuid.UUID) -> Plan:
        """Every collaborator call here is timeout-bounded (see
        `_with_timeout`): each is a stand-in for what will eventually be a
        real model/API call, and a hung one must fail this request rather
        than hang it forever."""
        intent = await self._with_timeout(
            self._intent_classifier.classify(request), stage="intent_classification"
        )
        await self._publish(evt.INTENT_DETERMINED, correlation_id, intent.model_dump())

        workflow = await self._with_timeout(
            self._workflow_resolver.resolve(intent, request), stage="workflow_resolution"
        )
        await self._publish(evt.WORKFLOW_DETERMINED, correlation_id, workflow.model_dump())

        agents = await self._with_timeout(
            self._agent_resolver.resolve(intent, workflow), stage="agent_resolution"
        )
        await self._publish(evt.AGENTS_DETERMINED, correlation_id, {"agents": [a.model_dump() for a in agents]})

        tools = await self._with_timeout(
            self._tool_resolver.resolve(intent, workflow), stage="tool_resolution"
        )
        await self._publish(evt.TOOLS_DETERMINED, correlation_id, {"tools": [t.model_dump() for t in tools]})

        memory = await self._with_timeout(
            self._memory_resolver.resolve(intent, workflow), stage="memory_resolution"
        )
        await self._publish(evt.MEMORY_DETERMINED, correlation_id, memory.model_dump())

        return Plan(
            request_id=request.id,
            correlation_id=correlation_id,
            intent=intent,
            workflow=workflow,
            agents=agents,
            tools=tools,
            memory=memory,
        )

    async def _with_timeout(self, awaitable: Awaitable[_T], *, stage: str) -> _T:
        """Bounds one planning-phase collaborator call so a hung or slow
        collaborator fails fast, naming the stage that hung, instead of
        hanging `handle_request` forever."""
        try:
            return await asyncio.wait_for(awaitable, timeout=self._planning_timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise PlanningTimeoutError(stage, self._planning_timeout_seconds) from exc

    # ------------------------------------------------------------------ #
    # Dispatch, monitor, retry
    # ------------------------------------------------------------------ #
    async def _dispatch_plan(self, request_id: uuid.UUID, plan: Plan) -> StructuredResponse:
        tasks = plan.build_tasks()
        results = await asyncio.gather(*(self._dispatch_with_retry(t) for t in tasks))

        overall_status = "completed" if all(r.status == "completed" for r in results) else "failed"
        await self._publish(
            evt.RUN_COMPLETED if overall_status == "completed" else evt.RUN_FAILED,
            plan.correlation_id,
            {"task_results": [r.model_dump() for r in results]},
        )
        return StructuredResponse(
            request_id=request_id,
            correlation_id=plan.correlation_id,
            status=overall_status,
            plan=plan,
            task_results=list(results),
            summary=self._summarize(overall_status, results),
        )

    async def _dispatch_with_retry(self, task: DispatchedTask) -> TaskResult:
        attempt = 1
        while True:
            task.attempts = attempt
            result = await self._dispatch_and_await(task)
            if result.status == "completed":
                return result
            if not self._retry_policy.should_retry(attempt, task.max_attempts):
                return result
            backoff = self._retry_policy.next_backoff(attempt)
            await self._publish(
                evt.TASK_RETRY_SCHEDULED,
                task.correlation_id,
                {"task_id": str(task.id), "attempt": attempt, "backoff_seconds": backoff},
            )
            if backoff > 0:
                await asyncio.sleep(backoff)
            attempt += 1

    async def _dispatch_and_await(self, task: DispatchedTask) -> TaskResult:
        """Dispatches one task and waits for the matching `task.completed`
        / `task.failed` event, timing out if the Task Queue never reports
        back -- a hung worker fails the task rather than hanging Commander
        forever."""
        loop = asyncio.get_event_loop()
        completion: asyncio.Future[TaskResult] = loop.create_future()

        async def on_completed(event: Event) -> None:
            if event.payload.get("task_id") == str(task.id) and not completion.done():
                completion.set_result(
                    TaskResult(task_id=task.id, status="completed", output=event.payload.get("output"))
                )

        async def on_failed(event: Event) -> None:
            if event.payload.get("task_id") == str(task.id) and not completion.done():
                completion.set_result(
                    TaskResult(task_id=task.id, status="failed", error=event.payload.get("error"))
                )

        # Subscribe before dispatching so a same-tick completion can never
        # race ahead of the subscription.
        await self._bus.subscribe(evt.TASK_COMPLETED, on_completed)
        await self._bus.subscribe(evt.TASK_FAILED, on_failed)
        try:
            await self._dispatcher.dispatch(task)
            await self._publish(
                evt.TASK_DISPATCHED,
                task.correlation_id,
                {"task_id": str(task.id), "kind": task.kind, "attempt": task.attempts},
            )
            try:
                return await asyncio.wait_for(completion, timeout=self._task_timeout_seconds)
            except asyncio.TimeoutError:
                return TaskResult(task_id=task.id, status="failed", error="timed out waiting for task completion")
        finally:
            await self._bus.unsubscribe(evt.TASK_COMPLETED, on_completed)
            await self._bus.unsubscribe(evt.TASK_FAILED, on_failed)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    async def _publish(self, event_type: str, correlation_id: uuid.UUID, payload: dict[str, Any]) -> None:
        await self._bus.publish(
            Event(event_type=event_type, source_module=SOURCE_MODULE, correlation_id=correlation_id, payload=payload)
        )

    @staticmethod
    def _summarize(status: str, results: list[TaskResult]) -> str:
        if status == "completed":
            return f"All {len(results)} task(s) completed successfully."
        failed = [r for r in results if r.status == "failed"]
        return f"{len(failed)} of {len(results)} task(s) failed."
