"""Workflow Engine -- turns a registered workflow definition into a
running, multi-step process.

Scheduling model: steps whose `depends_on` are all satisfied run
together in one "wave" via `asyncio.gather`; the next wave is whatever
becomes ready once that one finishes. Step sequencing and parallel steps
are the same mechanism seen from two angles -- a chain of single
dependencies is sequential, several steps with no dependency on each
other are parallel -- rather than two separate code paths.

Division of labor with Commander (see commander_bridge.py): Commander
plans a request and dispatches opaque tasks; it has no concept of a
step, a branch, or a parallel group, and nothing here duplicates that.
Commander's OWN approval gate (`resume_after_approval`) pauses an entire
plan before any dispatch; this engine's approval steps pause execution
*between* two already-dispatched steps within one in-flight run -- a
different scope, not a re-implementation.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.workflow_engine import events as evt
from hermes.modules.workflow_engine.contracts import CapabilitySelector, MemoryStore, ToolInvoker
from hermes.modules.workflow_engine.errors import (
    InvalidWorkflowDefinitionError,
    UnknownWorkflowError,
    UnknownWorkflowRunError,
    WorkflowEngineConfigError,
)
from hermes.modules.workflow_engine.models import StepDefinition, StepResult, WorkflowDefinition, WorkflowRun
from hermes.modules.workflow_engine.templating import resolve_templates
from hermes.modules.tool_manager.models import ToolInvocationRequest

SOURCE_MODULE = "workflow_engine"

_TERMINAL_STEP_STATUSES = {"completed", "failed", "skipped"}


class WorkflowEngine:
    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        tool_manager: ToolInvoker | None = None,
        memory_manager: MemoryStore | None = None,
        capability_registry: CapabilitySelector | None = None,
    ) -> None:
        """Every collaborator is optional. A workflow whose steps never
        need a given collaborator works with none configured; a step
        that DOES need one and doesn't find it fails clearly
        (`WorkflowEngineConfigError`) rather than crashing the engine."""
        self._bus = event_bus
        self._tool_manager = tool_manager
        self._memory_manager = memory_manager
        self._capability_registry = capability_registry
        self._definitions: dict[str, WorkflowDefinition] = {}
        self._runs: dict[uuid.UUID, WorkflowRun] = {}

    # ------------------------------------------------------------------ #
    # Workflow definitions
    # ------------------------------------------------------------------ #
    def register_workflow(self, definition: WorkflowDefinition) -> None:
        """Validates and registers a definition. Raises
        `InvalidWorkflowDefinitionError` for anything that would only
        fail confusingly at run time: duplicate step names, a
        `depends_on`/condition referencing an unknown step, a dependency
        cycle, a tool_call step missing exactly one of tool_name/
        capability, or a memory step missing scope/key."""
        self._validate_definition(definition)
        self._definitions[definition.workflow_id] = definition

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition:
        if workflow_id not in self._definitions:
            raise UnknownWorkflowError(workflow_id)
        return self._definitions[workflow_id]

    def _validate_definition(self, definition: WorkflowDefinition) -> None:
        names = [s.name for s in definition.steps]
        if len(names) != len(set(names)):
            raise InvalidWorkflowDefinitionError(definition.workflow_id, "duplicate step names")
        name_set = set(names)

        for step in definition.steps:
            for dep in step.depends_on:
                if dep not in name_set:
                    raise InvalidWorkflowDefinitionError(
                        definition.workflow_id, f"step {step.name!r} depends on unknown step {dep!r}"
                    )
            if step.condition is not None:
                if step.condition.step not in name_set:
                    raise InvalidWorkflowDefinitionError(
                        definition.workflow_id,
                        f"step {step.name!r} condition references unknown step {step.condition.step!r}",
                    )
                if step.condition.step not in step.depends_on:
                    raise InvalidWorkflowDefinitionError(
                        definition.workflow_id,
                        f"step {step.name!r} condition references {step.condition.step!r}, "
                        f"which must also be in its depends_on",
                    )
            if step.kind == "tool_call":
                if not step.tool_name and not step.capability:
                    raise InvalidWorkflowDefinitionError(
                        definition.workflow_id, f"tool_call step {step.name!r} needs tool_name or capability"
                    )
                if step.tool_name and step.capability:
                    raise InvalidWorkflowDefinitionError(
                        definition.workflow_id,
                        f"tool_call step {step.name!r} must set exactly one of tool_name/capability",
                    )
            if step.kind in ("memory_read", "memory_write") and (not step.memory_scope or not step.memory_key):
                raise InvalidWorkflowDefinitionError(
                    definition.workflow_id, f"{step.kind} step {step.name!r} needs memory_scope and memory_key"
                )

        self._check_no_cycles(definition)

    def _check_no_cycles(self, definition: WorkflowDefinition) -> None:
        graph = {s.name: s.depends_on for s in definition.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                raise InvalidWorkflowDefinitionError(
                    definition.workflow_id, f"dependency cycle detected at step {node!r}"
                )
            visiting.add(node)
            for dep in graph.get(node, []):
                dfs(dep)
            visiting.discard(node)
            visited.add(node)

        for name in graph:
            dfs(name)

    # ------------------------------------------------------------------ #
    # Runs
    # ------------------------------------------------------------------ #
    async def start_run(
        self, workflow_id: str, *, input: dict[str, Any] | None = None, requesting_agent_id: str = "system"
    ) -> WorkflowRun:
        definition = self.get_workflow(workflow_id)
        run = WorkflowRun(workflow_id=workflow_id, input=input or {}, requesting_agent_id=requesting_agent_id)
        self._runs[run.id] = run
        await self._publish(evt.RUN_STARTED, run, {})
        await self._advance(run, definition)
        return run

    async def approve_step(self, run_id: uuid.UUID, step_name: str, *, approved: bool, approver: str) -> WorkflowRun:
        """Resolves a `pending_approval` step and resumes the run. This
        is scoped to ONE step inside an already-running workflow -- not
        the same thing as Commander's plan-level approval gate, which
        happens before any dispatch at all."""
        run = self._require_run(run_id)
        definition = self.get_workflow(run.workflow_id)
        result = run.step_results.get(step_name)
        if result is None or result.status != "pending_approval":
            raise ValueError(f"step {step_name!r} is not awaiting approval")

        result.status = "completed" if approved else "failed"
        result.output = {"approved": approved, "approver": approver}
        result.ended_at = datetime.now(timezone.utc)
        await self._publish(
            evt.STEP_APPROVAL_DECIDED, run, {"step": step_name, "approved": approved, "approver": approver}
        )

        run.status = "running"
        await self._advance(run, definition)
        return run

    async def resume_run(self, run_id: uuid.UUID) -> WorkflowRun:
        """Failure recovery: forgets any `failed` step results and
        re-advances, so those steps are attempted fresh. Steps that
        already `completed` keep their results -- a resume never re-runs
        work that already succeeded."""
        run = self._require_run(run_id)
        definition = self.get_workflow(run.workflow_id)
        for name, result in list(run.step_results.items()):
            if result.status == "failed":
                del run.step_results[name]
        run.status = "running"
        await self._advance(run, definition)
        return run

    def get_run(self, run_id: uuid.UUID) -> WorkflowRun:
        """Synchronous by design, same rationale as State Manager's
        query methods: a pure in-memory read must never be blocked."""
        return self._require_run(run_id)

    def get_run_status(self, run_id: uuid.UUID) -> str:
        return self._require_run(run_id).status

    def _require_run(self, run_id: uuid.UUID) -> WorkflowRun:
        if run_id not in self._runs:
            raise UnknownWorkflowRunError(run_id)
        return self._runs[run_id]

    # ------------------------------------------------------------------ #
    # Scheduler
    # ------------------------------------------------------------------ #
    async def _advance(self, run: WorkflowRun, definition: WorkflowDefinition) -> None:
        steps_by_name = {s.name: s for s in definition.steps}
        while True:
            ready = [
                step
                for step in steps_by_name.values()
                if step.name not in run.step_results
                and all(
                    run.step_results.get(dep, StepResult(name=dep)).status in _TERMINAL_STEP_STATUSES
                    for dep in step.depends_on
                )
            ]
            if not ready:
                break
            await asyncio.gather(*(self._execute_step(run, step) for step in ready))
            if run.status == "awaiting_approval":
                return

        run.status = "failed" if any(r.status == "failed" for r in run.step_results.values()) else "completed"
        run.ended_at = datetime.now(timezone.utc)
        await self._publish(evt.RUN_COMPLETED if run.status == "completed" else evt.RUN_FAILED, run, {})

    def _dependencies_permit_execution(self, run: WorkflowRun, step: StepDefinition) -> bool:
        for dep in step.depends_on:
            dep_status = run.step_results[dep].status
            if dep_status == "completed":
                continue
            # The dependency didn't complete (failed/skipped). Only
            # proceed if this step's OWN condition specifically inspects
            # that dependency -- an error-handling branch -- otherwise
            # there's nothing sensible to run against, so skip.
            if step.condition is not None and step.condition.step == dep:
                continue
            return False
        return True

    def _evaluate_condition(self, run: WorkflowRun, condition) -> bool:
        result = run.step_results.get(condition.step)
        if result is None:
            return False
        target: Any = result.status if condition.path is None else _dig(result.output or {}, condition.path)
        return bool(target) if condition.equals is None else target == condition.equals

    async def _execute_step(self, run: WorkflowRun, step: StepDefinition) -> None:
        if not self._dependencies_permit_execution(run, step):
            run.step_results[step.name] = StepResult(name=step.name, status="skipped")
            await self._publish(evt.STEP_SKIPPED, run, {"step": step.name, "reason": "dependency not completed"})
            return

        if step.condition is not None and not self._evaluate_condition(run, step.condition):
            run.step_results[step.name] = StepResult(name=step.name, status="skipped")
            await self._publish(evt.STEP_SKIPPED, run, {"step": step.name, "reason": "condition not met"})
            return

        started_at = datetime.now(timezone.utc)
        run.step_results[step.name] = StepResult(name=step.name, status="running", started_at=started_at)
        await self._publish(evt.STEP_STARTED, run, {"step": step.name})

        if step.kind == "approval":
            run.step_results[step.name] = StepResult(name=step.name, status="pending_approval", started_at=started_at)
            run.status = "awaiting_approval"
            await self._publish(
                evt.STEP_APPROVAL_REQUESTED, run, {"step": step.name, "message": step.approval_message}
            )
            return

        attempt = 1
        while True:
            try:
                output = await asyncio.wait_for(self._run_step_action(run, step), timeout=step.timeout_seconds)
                run.step_results[step.name] = StepResult(
                    name=step.name,
                    status="completed",
                    output=output,
                    attempts=attempt,
                    started_at=started_at,
                    ended_at=datetime.now(timezone.utc),
                )
                await self._publish(evt.STEP_COMPLETED, run, {"step": step.name, "attempt": attempt})
                return
            except Exception as exc:  # noqa: BLE001 -- data for this step's own retry policy
                if not step.retry_policy.should_retry(attempt, step.retry_policy.max_attempts):
                    run.step_results[step.name] = StepResult(
                        name=step.name,
                        status="failed",
                        error=str(exc),
                        attempts=attempt,
                        started_at=started_at,
                        ended_at=datetime.now(timezone.utc),
                    )
                    await self._publish(evt.STEP_FAILED, run, {"step": step.name, "attempt": attempt, "error": str(exc)})
                    return
                backoff = step.retry_policy.next_backoff(attempt)
                await self._publish(
                    evt.STEP_RETRY_SCHEDULED, run, {"step": step.name, "attempt": attempt, "backoff_seconds": backoff}
                )
                if backoff > 0:
                    await asyncio.sleep(backoff)
                attempt += 1

    # ------------------------------------------------------------------ #
    # Step actions
    # ------------------------------------------------------------------ #
    async def _run_step_action(self, run: WorkflowRun, step: StepDefinition) -> dict[str, Any]:
        if step.kind == "noop":
            return {}
        if step.kind == "tool_call":
            return await self._run_tool_call(run, step)
        if step.kind == "memory_read":
            return await self._run_memory_read(run, step)
        if step.kind == "memory_write":
            return await self._run_memory_write(run, step)
        raise ValueError(f"unsupported step kind {step.kind!r}")

    def _step_outputs(self, run: WorkflowRun) -> dict[str, dict[str, Any] | None]:
        return {name: result.output for name, result in run.step_results.items()}

    async def _run_tool_call(self, run: WorkflowRun, step: StepDefinition) -> dict[str, Any]:
        tool_name = step.tool_name
        if tool_name is None:
            if self._capability_registry is None:
                raise WorkflowEngineConfigError(
                    f"step {step.name!r} resolves a capability but no CapabilitySelector is configured"
                )
            selection = await self._capability_registry.select(step.capability)
            if selection.selected is None:
                raise RuntimeError(f"no available provider for capability {step.capability!r}: {selection.reason}")
            tool_name = selection.selected

        if self._tool_manager is None:
            raise WorkflowEngineConfigError(f"step {step.name!r} is a tool_call but no ToolInvoker is configured")

        parameters = resolve_templates(step.parameters, input=run.input, step_outputs=self._step_outputs(run))
        request = ToolInvocationRequest(
            tool_name=tool_name, operation=step.operation or "", parameters=parameters, correlation_id=run.id
        )
        result = await self._tool_manager.invoke(request)
        if result.status == "failed":
            raise RuntimeError(result.error or "tool invocation failed")
        return result.output or {}

    async def _run_memory_read(self, run: WorkflowRun, step: StepDefinition) -> dict[str, Any]:
        if self._memory_manager is None:
            raise WorkflowEngineConfigError(f"step {step.name!r} is a memory_read but no MemoryStore is configured")
        memory_key = self._resolve_memory_key(run, step)
        entry = await self._memory_manager.get_by_key(
            requesting_agent_id=run.requesting_agent_id,
            scope=step.memory_scope,
            key=memory_key,
            owner_agent_id=step.memory_owner_agent_id,
            workflow_run_id=run.id if step.memory_scope == "workflow" else None,
        )
        return {"found": entry is not None, "value": entry.value if entry is not None else None}

    async def _run_memory_write(self, run: WorkflowRun, step: StepDefinition) -> dict[str, Any]:
        if self._memory_manager is None:
            raise WorkflowEngineConfigError(f"step {step.name!r} is a memory_write but no MemoryStore is configured")
        memory_key = self._resolve_memory_key(run, step)
        value = resolve_templates(step.memory_value_template, input=run.input, step_outputs=self._step_outputs(run))
        entry = await self._memory_manager.save(
            requesting_agent_id=run.requesting_agent_id,
            scope=step.memory_scope,
            key=memory_key,
            value=value,
            owner_agent_id=step.memory_owner_agent_id,
            workflow_run_id=run.id if step.memory_scope == "workflow" else None,
        )
        return {"entry_id": str(entry.id)}

    def _resolve_memory_key(self, run: WorkflowRun, step: StepDefinition) -> str:
        """`memory_key` supports the same `{{input.<path>}}` /
        `{{steps.<name>.output.<path>}}` templates as `parameters` and
        `memory_value_template` -- e.g. `"research_brief/{{input.topic}}"`
        -- so a single generic workflow definition can address a
        different memory entry per run instead of sharing one fixed
        slot. A key with no template syntax resolves unchanged."""
        resolved = resolve_templates(step.memory_key, input=run.input, step_outputs=self._step_outputs(run))
        return str(resolved)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    async def _publish(self, event_type: str, run: WorkflowRun, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module=SOURCE_MODULE,
                correlation_id=run.id,
                payload={"run_id": str(run.id), "workflow_id": run.workflow_id, **payload},
            )
        )


def _dig(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node
