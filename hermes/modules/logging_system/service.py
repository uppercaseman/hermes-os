"""LoggingSystem -- captures, stores, queries, and replays system
activity by subscribing to every event on the Event Bus.

Passive by design: it never dispatches, never orchestrates, never
changes another module's behavior or state. It's the one module allowed
to be a silent observer of literally everything -- exactly the role the
very first architecture document anticipated for it. Nothing here
imports Commander, Mission System, Workflow Engine, Task Queue, State
Manager, or Tool Manager; Logging System depends only on the Event
Bus's own `Event` model, so it needs no changes when any of those
modules changes.

Mission-level querying works "for free" across every module: Mission
System sets `correlation_id = mission.id` on every request it dispatches
(see task_queue's README for the same convention), so any event
downstream of a mission-originated request already shares that
correlation_id -- `query(mission_id=...)` checks both the payload's
explicit `mission_id` field (present on Mission System's own events) and
a `correlation_id` match (present on everything else), which is robust
regardless of which order events actually arrive in.

Workflow-level querying is scoped honestly to Workflow Engine's own
events (which already carry `run_id` in their payload) rather than
reaching into Task Queue to add a matching field there too -- see
README.md's "known gaps" section for why that boundary was drawn where
it was.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.logging_system.backends import InMemoryLogBackend
from hermes.modules.logging_system.contracts import LogStorageBackend
from hermes.modules.logging_system.errors import UnknownLogEntryError
from hermes.modules.logging_system.models import LogEntry
from hermes.modules.logging_system.redaction import RedactionHook, default_redactor
from hermes.modules.logging_system.severity import classify_severity


class LoggingSystem:
    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        backend: LogStorageBackend | None = None,
        redaction_hook: RedactionHook | None = None,
    ) -> None:
        self._bus = event_bus
        self._backend = backend or InMemoryLogBackend()
        self._redact = redaction_hook or default_redactor
        self._subscribed = False

    async def start(self) -> None:
        """Subscribes to every event on the bus. A no-op if no event bus
        was given, or if already started."""
        if self._bus is not None and not self._subscribed:
            await self._bus.subscribe("*", self.capture)
            self._subscribed = True

    async def stop(self) -> None:
        if self._bus is not None and self._subscribed:
            await self._bus.unsubscribe("*", self.capture)
            self._subscribed = False

    # ------------------------------------------------------------------ #
    # Capture
    # ------------------------------------------------------------------ #
    async def capture(self, event: Event) -> None:
        """Structures, redacts, and persists one event. Public so it can
        be called directly -- for tests, or for logging something that
        didn't arrive via the bus -- not just as the subscription
        callback."""
        payload = self._redact(dict(event.payload))
        tool_name = payload.get("tool_name")
        entry = LogEntry(
            event_type=event.event_type,
            source_module=event.source_module,
            correlation_id=event.correlation_id,
            severity=classify_severity(event.event_type, event.level),
            payload=payload,
            mission_id=self._as_uuid(event.payload.get("mission_id")),
            workflow_run_id=self._as_uuid(event.payload.get("run_id")),
            task_id=self._as_uuid(event.payload.get("task_id")),
            tool_name=tool_name if isinstance(tool_name, str) else None,
            captured_at=event.ts,
        )
        await self._backend.save(entry)

    @staticmethod
    def _as_uuid(raw: Any) -> uuid.UUID | None:
        if not raw:
            return None
        try:
            return uuid.UUID(str(raw))
        except (ValueError, TypeError, AttributeError):
            return None

    # ------------------------------------------------------------------ #
    # Queries -- async, unlike some other modules' sync query methods,
    # because this module's backend is a genuinely pluggable, possibly
    # I/O-bound Protocol (matching Task Queue's design), not a plain
    # in-process dict the way State Manager/Workflow Engine's are.
    # ------------------------------------------------------------------ #
    async def query(
        self,
        *,
        source_module: str | None = None,
        event_type: str | None = None,
        severity: str | None = None,
        correlation_id: uuid.UUID | None = None,
        mission_id: uuid.UUID | None = None,
        workflow_run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        tool_name: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[LogEntry]:
        """Every filter is AND-ed together. Returns entries in
        chronological order. Never raises -- an empty result is a valid
        outcome for a filter query."""
        results = []
        for entry in await self._backend.list_all():
            if source_module is not None and entry.source_module != source_module:
                continue
            if event_type is not None and entry.event_type != event_type:
                continue
            if severity is not None and entry.severity != severity:
                continue
            if correlation_id is not None and entry.correlation_id != correlation_id:
                continue
            if mission_id is not None and not (entry.mission_id == mission_id or entry.correlation_id == mission_id):
                continue
            if workflow_run_id is not None and entry.workflow_run_id != workflow_run_id:
                continue
            if task_id is not None and entry.task_id != task_id:
                continue
            if tool_name is not None and entry.tool_name != tool_name:
                continue
            if since is not None and entry.captured_at < since:
                continue
            if until is not None and entry.captured_at > until:
                continue
            results.append(entry)
        results.sort(key=lambda e: e.captured_at)
        return results

    async def get_entry(self, entry_id: uuid.UUID) -> LogEntry:
        entry = await self._backend.get(entry_id)
        if entry is None:
            raise UnknownLogEntryError(entry_id)
        return entry

    async def list_by_mission(self, mission_id: uuid.UUID) -> list[LogEntry]:
        return await self.query(mission_id=mission_id)

    async def list_by_workflow_run(self, workflow_run_id: uuid.UUID) -> list[LogEntry]:
        return await self.query(workflow_run_id=workflow_run_id)

    async def list_by_task(self, task_id: uuid.UUID) -> list[LogEntry]:
        return await self.query(task_id=task_id)

    async def list_by_tool(self, tool_name: str) -> list[LogEntry]:
        return await self.query(tool_name=tool_name)

    async def list_errors(self) -> list[LogEntry]:
        return await self.query(severity="error")

    async def list_health_logs(self) -> list[LogEntry]:
        """State Manager's and Supervisor's own events -- the "health/
        status logs" the module-health story is already told through."""
        entries = await self._backend.list_all()
        health = [e for e in entries if e.source_module in ("state_manager", "supervisor")]
        health.sort(key=lambda e: e.captured_at)
        return health

    # ------------------------------------------------------------------ #
    # Replay
    # ------------------------------------------------------------------ #
    async def replay(self, correlation_id: uuid.UUID) -> list[LogEntry]:
        """Every entry sharing one correlation_id, in chronological
        order -- reconstructing exactly what happened for one request,
        mission, or workflow run."""
        return await self.query(correlation_id=correlation_id)

    def render_replay(self, entries: list[LogEntry]) -> str:
        """A human-readable timeline, for a debugging session or a log
        dump -- not a structured format; `export`/`export_json` are for
        that."""
        lines = [
            f"[{entry.captured_at.isoformat()}] {entry.source_module:<20} "
            f"{entry.severity:<6} {entry.event_type:<40} {entry.payload}"
            for entry in entries
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Export -- the "future UI/dashboard" hook: plain, JSON-serializable
    # data, the same pattern State Manager's SystemDiagnostics and
    # Workflow Engine's WorkflowRun already use.
    # ------------------------------------------------------------------ #
    async def export(self, **filters: Any) -> list[dict[str, Any]]:
        entries = await self.query(**filters)
        return [entry.model_dump(mode="json") for entry in entries]

    async def export_json(self, **filters: Any) -> str:
        return json.dumps(await self.export(**filters), indent=2)
