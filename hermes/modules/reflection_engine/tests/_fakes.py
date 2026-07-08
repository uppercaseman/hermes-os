"""Test doubles for the Reflection Engine.

Every collaborator the engine can hold has a fake here:
- `FakeMemoryWriter`: query/record/mark_superseded. Records every
  operation in `_operations` so tests can assert the engine's calls
  rather than only its final state.
- `FakeLogQuerier`: query/list_errors. Returns whatever entries
  tests seed into `_entries`.
- `FakeWorkingMemoryReader`: query. Same shape.
- `FakeEventBus`: publish/subscribe/unsubscribe. Captures every event
  in `_published` for assertions.

The fakes are deliberately minimal -- they implement only the
Protocol methods, no extras -- so a Protocol violation would be
caught at the type-check level (via type hints on the engine's
collaborator parameters) rather than via runtime AttributeError.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from hermes.core.event_bus.models import Event


@dataclass
class MemoryEntry:
    """Stand-in for `MemoryManager`'s `MemoryEntry`."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    scope: str = "persistent"
    owner_agent_id: str | None = None
    session_id: str | None = None
    workflow_run_id: uuid.UUID | None = None
    key: str = ""
    value: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    backlinks: list[uuid.UUID] = field(default_factory=list)
    created_at: str = ""
    # Sprint-2 typed fields. All default None / empty -- the fake is
    # a minimal stand-in for the real Pydantic model and the existing
    # tests don't read these fields, so the defaults preserve Sprint-1
    # behaviour.
    memory_type: str | None = None
    confidence: float | None = None
    importance: float | None = None
    provenance: list[Any] = field(default_factory=list)
    superseded_by: uuid.UUID | None = None
    relationships: list[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.created_at:
            from datetime import datetime, timezone
            self.created_at = datetime.now(timezone.utc).isoformat()


class FakeMemoryWriter:
    def __init__(self) -> None:
        self.entries: list[MemoryEntry] = []
        self._operations: list[tuple[str, dict[str, Any]]] = []

    async def query(
        self,
        *,
        requesting_agent_id: str,
        scope: str | None = None,
        tags: list[str] | None = None,
        owner_agent_id: str | None = None,
        session_id: str | None = None,
        workflow_run_id: uuid.UUID | None = None,
    ) -> list[MemoryEntry]:
        self._operations.append(("query", {"scope": scope, "tags": tags, "session_id": session_id, "workflow_run_id": workflow_run_id}))
        out = []
        for entry in self.entries:
            if scope is not None and entry.scope != scope:
                continue
            if session_id is not None and entry.session_id != session_id:
                continue
            if workflow_run_id is not None and entry.workflow_run_id != workflow_run_id:
                continue
            if owner_agent_id is not None and entry.owner_agent_id != owner_agent_id:
                continue
            if tags is not None and not all(t in entry.tags for t in tags):
                continue
            out.append(entry)
        return out

    async def record(
        self,
        *,
        requesting_agent_id: str,
        scope: str,
        key: str,
        value: dict[str, Any],
        owner_agent_id: str | None = None,
        tags: list[str] | None = None,
        backlinks: list[uuid.UUID] | None = None,
    ) -> MemoryEntry:
        # Upsert by (scope, owner, key) -- mirrors MemoryManager.save.
        for entry in self.entries:
            if entry.scope == scope and entry.owner_agent_id == owner_agent_id and entry.key == key:
                entry.value = value
                if tags is not None:
                    entry.tags = tags
                if backlinks is not None:
                    entry.backlinks = backlinks
                self._operations.append(("record_update", {"key": key, "scope": scope}))
                return entry
        entry = MemoryEntry(
            scope=scope,
            owner_agent_id=owner_agent_id,
            key=key,
            value=value,
            tags=tags or [],
            backlinks=backlinks or [],
        )
        self.entries.append(entry)
        self._operations.append(("record_insert", {"key": key, "scope": scope}))
        return entry

    async def record_typed(
        self,
        *,
        requesting_agent_id: str,
        memory_type: str,
        key: str,
        value: dict[str, Any],
        scope: str | None = None,
        confidence: float | None = None,
        importance: float | None = None,
        provenance: list[Any] | None = None,
        relationships: list[Any] | None = None,
        owner_agent_id: str | None = None,
        session_id: str | None = None,
        workflow_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        backlinks: list[uuid.UUID] | None = None,
        ttl_seconds: float | None = None,
        origin_mission_id: uuid.UUID | None = None,
    ) -> MemoryEntry:
        """Sprint-2 typed write. Mirrors MemoryManager.record_typed
        semantically -- upsert by (scope, owner, key) with first-class
        typed fields persisted on the entry. Defaults `scope` to
        `persistent` when the caller passes None (the engine passes
        its own scope explicitly, so this fallback is mostly for
        tests)."""
        resolved_scope = scope or "persistent"
        for entry in self.entries:
            if (
                entry.scope == resolved_scope
                and entry.owner_agent_id == owner_agent_id
                and entry.key == key
            ):
                entry.value = value
                entry.memory_type = memory_type
                if confidence is not None:
                    entry.confidence = confidence
                if importance is not None:
                    entry.importance = importance
                if provenance is not None:
                    entry.provenance = list(provenance)
                if relationships is not None:
                    entry.relationships = list(relationships)
                if tags is not None:
                    entry.tags = list(tags)
                if backlinks is not None:
                    entry.backlinks = list(backlinks)
                self._operations.append(("record_typed_update", {"key": key, "scope": resolved_scope, "memory_type": memory_type}))
                return entry
        entry = MemoryEntry(
            scope=resolved_scope,
            owner_agent_id=owner_agent_id,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            key=key,
            value=value,
            tags=tags or [],
            backlinks=backlinks or [],
            memory_type=memory_type,
            confidence=confidence,
            importance=importance,
            provenance=list(provenance or []),
            relationships=list(relationships or []),
        )
        self.entries.append(entry)
        self._operations.append(("record_typed_insert", {"key": key, "scope": resolved_scope, "memory_type": memory_type}))
        return entry

    async def mark_superseded(
        self,
        *,
        requesting_agent_id: str,
        entry_id: uuid.UUID,
        superseded_by: uuid.UUID,
    ) -> None:
        for entry in self.entries:
            if entry.id == entry_id:
                entry.value["superseded_by"] = str(superseded_by)
                if "superseded" not in entry.tags:
                    entry.tags.append("superseded")
                self._operations.append(("mark_superseded", {"entry_id": str(entry_id), "superseded_by": str(superseded_by)}))
                return


class FakeLogQuerier:
    def __init__(self) -> None:
        self.entries: list[Any] = []

    async def query(
        self,
        *,
        mission_id: uuid.UUID | None = None,
        correlation_id: uuid.UUID | None = None,
        severity: str | None = None,
        since: Any = None,
        until: Any = None,
    ) -> list[Any]:
        out = []
        for e in self.entries:
            if mission_id is not None and getattr(e, "mission_id", None) != mission_id:
                continue
            if severity is not None and getattr(e, "severity", None) != severity:
                continue
            out.append(e)
        return out

    async def list_errors(self) -> list[Any]:
        return [e for e in self.entries if getattr(e, "severity", None) == "error"]


class FakeWorkingMemoryReader:
    def __init__(self) -> None:
        self.entries: list[MemoryEntry] = []

    async def query(
        self,
        *,
        requesting_agent_id: str,
        session_id: str | None = None,
        scope: str | None = None,
        tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        out = []
        for e in self.entries:
            if session_id is not None and e.session_id != session_id:
                continue
            if scope is not None and e.scope != scope:
                continue
            if tags is not None and not all(t in e.tags for t in tags):
                continue
            out.append(e)
        return out


class FakeEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[Event], Any]]] = {}
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)
        # Deliver synchronously to local subscribers (matches the
        # `InMemoryEventBus` happens-before contract for tests).
        for handler in list(self._subscribers.get(event.event_type, [])) + list(self._subscribers.get("*", [])):
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001
                # Tests can opt into checking failures via
                # `handler_errors`; the bus itself swallows them to
                # match the production contract.
                pass

    async def subscribe(self, event_type: str, handler: Callable[[Event], Any]) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    async def unsubscribe(self, event_type: str, handler: Callable[[Event], Any]) -> None:
        if event_type in self._subscribers and handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    def by_type(self, event_type: str) -> list[Event]:
        return [e for e in self.published if e.event_type == event_type]


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def make_log_entry(
    *,
    event_type: str,
    mission_id: uuid.UUID,
    severity: str = "info",
    tool_name: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    """A stand-in for `LogEntry` shaped like the things the default
    extractor inspects. The reflection engine only reads attributes,
    so a SimpleNamespace is enough."""

    return type(
        "LogEntry",
        (),
        {
            "id": uuid.uuid4(),
            "event_type": event_type,
            "mission_id": mission_id,
            "severity": severity,
            "tool_name": tool_name,
            "payload": payload or {},
        },
    )()


def make_event(
    *,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source_module: str = "test",
    correlation_id: uuid.UUID | None = None,
) -> Event:
    return Event(
        event_type=event_type,
        source_module=source_module,
        correlation_id=correlation_id or uuid.uuid4(),
        level="info",
        payload=payload or {},
    )