"""State Manager -- the canonical, Commander-facing record of every
module's health and lifecycle.

This is deliberately a layer ON TOP OF the Supervisor (core/supervisor),
not a duplicate of it. The two solve different problems:

- **Supervisor** answers "is this module's process alive, and if not,
  restart it" -- a fast, tight crash-loop with bounded backoff, driven by
  polling `health_check()`.
- **State Manager** answers "what is every module's CURRENT, richer state
  right now, considering its own self-reported workload and its
  dependencies" -- driven by modules actively pushing a **heartbeat**,
  which is the only way anything in Hermes can know the difference
  between "busy" and "idle" (Supervisor's `health_check()` only ever
  returns a bool; it has no concept of workload).

Because most modules don't push heartbeats yet, State Manager also
listens to the exact same Supervisor lifecycle events Tool Manager and
the Capability Registry already consume, translating them into a
baseline state so every module is trackable from day one. A module that
has NEVER called `report_heartbeat()` directly is in "passive" mode: its
state comes only from Supervisor events, which are transition-driven, not
periodic -- so passive modules are deliberately exempt from the
heartbeat-staleness sweep (the absence of a new Supervisor event means
"nothing changed," not "gone silent"). Once a module calls
`report_heartbeat()` even once, it graduates to "active" mode and the
staleness timeout applies to it from then on.

Automatic recovery here is a second, SLOWER tier above Supervisor's own:
Supervisor already retries a crashing unit fast, with its own bounded
backoff. State Manager only steps in when Supervisor has definitively
given up (`unit.restart_exhausted` / `unit.restart_skipped`) or when an
active module's heartbeat goes stale (implying it's alive-but-unresponsive
in a way Supervisor's own health_check can't see) -- bounded by its own
`RetryPolicy` (the same reusable building block used for task retry,
module restart, and tool-call retry).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.core.state_manager import events as evt
from hermes.core.state_manager.errors import UnknownModuleError
from hermes.core.state_manager.models import (
    Heartbeat,
    ModuleDiagnostics,
    ModuleState,
    RestartRequest,
    SystemDiagnostics,
)
from hermes.core.supervisor import events as supervisor_events
from hermes.core.supervisor.policy import RetryPolicy
from hermes.core.supervisor.service import Supervisor

SOURCE_MODULE = "state_manager"

HeartbeatSource = Literal["active", "supervisor_derived"]

# How a Supervisor unit-lifecycle event translates into this module's own
# 7-state vocabulary. `crashed`/`restarting` map to "restarting" (not
# "failed") because Supervisor is already handling those -- State Manager
# only escalates to its own recovery once Supervisor has given up.
_SUPERVISOR_EVENT_STATE: dict[str, ModuleState] = {
    supervisor_events.UNIT_STARTED: "healthy",
    supervisor_events.UNIT_UNHEALTHY: "degraded",
    supervisor_events.UNIT_CRASHED: "restarting",
    supervisor_events.UNIT_RESTARTING: "restarting",
    supervisor_events.UNIT_RESTART_EXHAUSTED: "failed",
    supervisor_events.UNIT_RESTART_SKIPPED: "failed",
    supervisor_events.UNIT_STOPPED: "offline",
}

_AUTO_RECOVER_TRIGGERS = {supervisor_events.UNIT_RESTART_EXHAUSTED, supervisor_events.UNIT_RESTART_SKIPPED}

_NON_DEGRADABLE_STATES = {"failed", "offline", "restarting"}
_UNMET_DEPENDENCY_STATES = {"failed", "offline"}


class StateManager:
    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        supervisor: Supervisor | None = None,
        heartbeat_timeout_seconds: float = 30.0,
        sweep_interval_seconds: float = 10.0,
        recovery_policy: RetryPolicy | None = None,
    ) -> None:
        """`supervisor` is optional: without one, `request_restart` only
        records the request (for something else to act on) rather than
        carrying it out. `event_bus` is optional too: without one, health
        must be pushed entirely through `report_heartbeat`."""
        self._bus = event_bus
        self._supervisor = supervisor
        self._heartbeat_timeout = heartbeat_timeout_seconds
        self._sweep_interval = sweep_interval_seconds
        self._recovery_policy = recovery_policy or RetryPolicy()

        self._known_modules: set[str] = set()
        self._dependencies: dict[str, set[str]] = {}
        self._auto_recover: dict[str, bool] = {}
        self._heartbeats: dict[str, Heartbeat] = {}
        self._heartbeat_sources: dict[str, HeartbeatSource] = {}
        self._restart_history: dict[str, list[RestartRequest]] = {}
        self._recovery_attempts: dict[str, int] = {}

        self._subscribed = False
        self._sweep_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Subscribes to Supervisor lifecycle events (if an event bus was
        given) and starts the heartbeat-staleness sweep loop (which runs
        regardless of whether a bus was given -- it only concerns
        actively-reporting modules)."""
        if self._bus is not None and not self._subscribed:
            await self._bus.subscribe("*", self._on_bus_event)
            self._subscribed = True
        if self._sweep_task is None:
            self._sweep_task = asyncio.ensure_future(self._sweep_loop())

    async def stop(self) -> None:
        """Undoes `start()`. Safe to call even if never started."""
        if self._bus is not None and self._subscribed:
            await self._bus.unsubscribe("*", self._on_bus_event)
            self._subscribed = False
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            self._sweep_task = None

    # ------------------------------------------------------------------ #
    # Declaration -- config, not runtime state. Sync, idempotent replace.
    # ------------------------------------------------------------------ #
    def declare_module(
        self, module_name: str, *, depends_on: list[str] | None = None, auto_recover: bool = True
    ) -> None:
        """Pre-registers `module_name` with its dependencies and recovery
        policy. Not required before `report_heartbeat`/Supervisor events
        start arriving for it -- those auto-declare with defaults (no
        dependencies, `auto_recover=True`) -- but this is how you set
        dependencies or opt a module out of automatic recovery."""
        self._known_modules.add(module_name)
        self._dependencies[module_name] = set(depends_on or [])
        self._auto_recover[module_name] = auto_recover

    # ------------------------------------------------------------------ #
    # Heartbeat (active reporting)
    # ------------------------------------------------------------------ #
    async def report_heartbeat(self, module_name: str, state: ModuleState, *, detail: str | None = None) -> None:
        """Called by a module (or a wrapper around it) to report its own
        current state -- the only way "busy" vs "idle" can ever be known,
        since Supervisor's `health_check()` has no concept of workload.
        Graduates `module_name` to "active" heartbeat mode, subjecting it
        to the staleness timeout from now on."""
        await self._set_state(module_name, state, source="active", detail=detail)

    # ------------------------------------------------------------------ #
    # Queries -- synchronous and side-effect-free by design, so Commander
    # (or anything else) can call them at any time without ever being
    # blocked by this module's own bookkeeping.
    # ------------------------------------------------------------------ #
    def get_state(self, module_name: str) -> ModuleState:
        """The EFFECTIVE state (after dependency-degradation is applied),
        not just the last raw report. Raises `UnknownModuleError` for a
        name that has never been declared or heartbeat from."""
        if module_name not in self._known_modules:
            raise UnknownModuleError(module_name)
        return self._effective_state(module_name)

    def get_state_all(self) -> dict[str, ModuleState]:
        return {name: self._effective_state(name) for name in self._known_modules}

    def diagnostics(self, module_name: str) -> ModuleDiagnostics:
        if module_name not in self._known_modules:
            raise UnknownModuleError(module_name)
        heartbeat = self._heartbeats.get(module_name)
        deps = sorted(self._dependencies.get(module_name, set()))
        history = self._restart_history.get(module_name, [])
        stale = (
            heartbeat is not None
            and self._heartbeat_sources.get(module_name) == "active"
            and self._is_stale(heartbeat)
        )
        return ModuleDiagnostics(
            module_name=module_name,
            reported_state=self._reported_state(module_name),
            effective_state=self._effective_state(module_name),
            last_heartbeat_at=heartbeat.reported_at if heartbeat else None,
            heartbeat_stale=stale,
            dependencies=deps,
            unmet_dependencies=[d for d in deps if self._reported_state(d) in _UNMET_DEPENDENCY_STATES],
            restart_count=len(history),
            last_restart_reason=history[-1].reason if history else None,
        )

    def diagnostics_all(self) -> SystemDiagnostics:
        modules = [self.diagnostics(name) for name in sorted(self._known_modules)]
        if any(m.effective_state == "failed" for m in modules):
            overall: Literal["healthy", "degraded", "critical"] = "critical"
        elif any(m.effective_state in ("degraded", "offline", "restarting") for m in modules):
            overall = "degraded"
        else:
            overall = "healthy"
        return SystemDiagnostics(generated_at=datetime.now(timezone.utc), modules=modules, overall_state=overall)

    # ------------------------------------------------------------------ #
    # Restart requests / recovery
    # ------------------------------------------------------------------ #
    async def request_restart(
        self, module_name: str, *, reason: str | None = None, requested_by: str = "unknown"
    ) -> RestartRequest:
        """Records the request and, if a Supervisor was provided, carries
        it out (stop then start). Without one, the request is recorded
        and published for something else to act on -- never raises for
        the underlying restart failing; that's reflected in the returned
        request's `status` instead."""
        self._known_modules.add(module_name)
        request = RestartRequest(module_name=module_name, reason=reason, requested_by=requested_by)
        self._restart_history.setdefault(module_name, []).append(request)
        await self._publish(
            evt.RESTART_REQUESTED,
            module_name,
            {"request_id": str(request.id), "reason": reason, "requested_by": requested_by},
        )

        current_source = self._heartbeat_sources.get(module_name, "supervisor_derived")
        await self._set_state(module_name, "restarting", source=current_source, detail=reason)

        if self._supervisor is not None:
            try:
                await self._supervisor.stop(module_name)
                await self._supervisor.start(module_name)
                request.status = "completed"
            except Exception as exc:  # noqa: BLE001 -- reflected on the request, never raised
                request.status = "failed"
                await self._publish(
                    evt.RESTART_FAILED, module_name, {"request_id": str(request.id), "error": str(exc)}
                )
        return request

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _set_state(
        self, module_name: str, state: ModuleState, *, source: HeartbeatSource, detail: str | None = None
    ) -> None:
        self._known_modules.add(module_name)
        self._dependencies.setdefault(module_name, set())
        self._auto_recover.setdefault(module_name, True)
        self._heartbeats[module_name] = Heartbeat(module_name=module_name, state=state, detail=detail)
        self._heartbeat_sources[module_name] = source
        if state == "healthy":
            self._recovery_attempts[module_name] = 0
        await self._publish(evt.STATE_REPORTED, module_name, {"state": state, "source": source, "detail": detail})

    def _reported_state(self, module_name: str) -> ModuleState:
        heartbeat = self._heartbeats.get(module_name)
        return heartbeat.state if heartbeat is not None else "offline"

    def _effective_state(self, module_name: str) -> ModuleState:
        reported = self._reported_state(module_name)
        if reported in _NON_DEGRADABLE_STATES:
            return reported
        # Uses each dependency's RAW reported state, never its own
        # effective state -- this is what keeps a cyclic dependency
        # declaration (A depends on B, B depends on A) from recursing
        # forever; it also means degradation doesn't cascade past one hop.
        deps = self._dependencies.get(module_name, set())
        if any(self._reported_state(dep) in _UNMET_DEPENDENCY_STATES for dep in deps):
            return "degraded"
        return reported

    def _is_stale(self, heartbeat: Heartbeat) -> bool:
        age = (datetime.now(timezone.utc) - heartbeat.reported_at).total_seconds()
        return age > self._heartbeat_timeout

    async def _sweep_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._sweep_interval)
                for module_name in list(self._known_modules):
                    if self._heartbeat_sources.get(module_name) != "active":
                        continue  # passive/derived modules are exempt -- see module docstring
                    heartbeat = self._heartbeats.get(module_name)
                    if heartbeat is None or heartbeat.state == "offline" or not self._is_stale(heartbeat):
                        continue
                    await self._set_state(module_name, "offline", source="active", detail="heartbeat timeout")
                    if self._auto_recover.get(module_name, True):
                        await self._maybe_auto_recover(module_name, reason="heartbeat timeout")
        except asyncio.CancelledError:
            return

    async def _on_bus_event(self, event: Event) -> None:
        state = _SUPERVISOR_EVENT_STATE.get(event.event_type)
        if state is None:
            return
        module_name = event.payload.get("unit")
        if not module_name:
            return
        current_source = self._heartbeat_sources.get(module_name)
        source: HeartbeatSource = current_source if current_source == "active" else "supervisor_derived"
        await self._set_state(module_name, state, source=source, detail=f"supervisor: {event.event_type}")

        if event.event_type in _AUTO_RECOVER_TRIGGERS and self._auto_recover.get(module_name, True):
            await self._maybe_auto_recover(module_name, reason=f"supervisor: {event.event_type}")

    async def _maybe_auto_recover(self, module_name: str, *, reason: str) -> None:
        attempt = self._recovery_attempts.get(module_name, 0) + 1
        if not self._recovery_policy.should_retry(attempt, self._recovery_policy.max_attempts):
            await self._publish(evt.RECOVERY_EXHAUSTED, module_name, {"attempts": attempt})
            return
        self._recovery_attempts[module_name] = attempt
        await self.request_restart(module_name, reason=reason, requested_by="state_manager_auto_recovery")

    async def _publish(self, event_type: str, module_name: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module=SOURCE_MODULE,
                correlation_id=uuid.uuid4(),
                payload={"module": module_name, **payload},
            )
        )
