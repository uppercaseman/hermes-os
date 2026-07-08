"""Protocols and inter-module contracts for the Reflection Engine.

Anything the engine imports across a module boundary is named here, so
a reviewer can see the full external surface in one file (mirrors
`Standards/Module Layout`'s contracts-file rationale).

Deliberate dependency choices:

- The engine speaks to Memory Manager through a `MemoryWriter` Protocol
  that is a *subset* of `MemoryManager`'s public surface. This keeps
  the engine from depending on Memory Manager's permissions machinery,
  redaction, sweep, or vector-search hooks -- a future alternative
  Memory Manager implementation that satisfies the subset will work
  with no engine change.
- The engine speaks to Logging System through a `LogQuerier` Protocol
  that exposes only the `query(...)` and `list_errors()` methods it
  actually needs. Same rationale.
- The engine publishes through whatever `EventBus` is given, exactly
  like every other module.
- The engine signals Mission System by **publishing** an
  `engine.reflection.completed` event with the outcome's
  `pending_approvals` count. The Mission System today does not
  consume that event (a future Sprint will wire the Dissolved
  transition to it), so the engine does not depend on Mission System
  directly.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol


class MemoryWriter(Protocol):
    """The subset of Memory Manager the engine actually uses.

    `query(...)` is read-only context for duplicate / contradiction
    detection. `record_typed(...)` is the Sprint-2 typed write path
    Phase 6 commits through -- the canonical store for the four
    destinations is the first-class `memory_type` field, not a
    tag encoding. `mark_superseded(...)` is the additive-only
    supersession primitive -- the old entry is never deleted; only
    its `superseded_by` is set.

    Mirrors `MemoryManager`'s public methods but is a Protocol so the
    engine never imports `MemoryManager` directly; tests pass a fake.

    The Sprint-1 `record(...)` surface is retained for callers that
    haven't migrated; `MemoryManager.save(...)` still satisfies
    the structural Protocol shape. The engine itself uses
    `record_typed(...)`.
    """

    async def query(
        self,
        *,
        requesting_agent_id: str,
        scope: str | None = None,
        tags: list[str] | None = None,
        owner_agent_id: str | None = None,
        session_id: str | None = None,
        workflow_run_id: uuid.UUID | None = None,
    ) -> list[Any]:
        ...

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
    ) -> Any:
        ...

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
    ) -> Any:
        ...

    async def mark_superseded(
        self,
        *,
        requesting_agent_id: str,
        entry_id: uuid.UUID,
        superseded_by: uuid.UUID,
    ) -> None:
        ...


class LogQuerier(Protocol):
    """The subset of Logging System the engine needs for Phase 1
    harvest. `list_errors()` is the "errors happened" signal; `query`
    is the broader event history for the mission."""

    async def query(
        self,
        *,
        mission_id: uuid.UUID | None = None,
        correlation_id: uuid.UUID | None = None,
        severity: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Any]:
        ...

    async def list_errors(self) -> list[Any]:
        ...


class WorkingMemoryReader(Protocol):
    """The subset of Memory Manager used by Phase 1 harvest to read
    the mission's session-scoped Working Memory entries.

    Kept as a separate Protocol from `MemoryWriter` so an engine
    integration that has different reader / writer collaborators (e.g.
    a remote Memory Manager and an in-process Working Memory cache)
    can wire each independently.
    """

    async def query(
        self,
        *,
        requesting_agent_id: str,
        session_id: str | None = None,
        scope: str | None = None,
        tags: list[str] | None = None,
    ) -> list[Any]:
        ...


class ReflectionApprover(Protocol):
    """The Phase-5 surface a human (or a future automated approver)
    uses to act on approval-required candidates. Today the only
    implementation is the engine itself -- `approve_candidate(...)`
    and `reject_candidate(...)` are methods on the engine. This
    Protocol exists so the same surface can be re-exported to a future
    dashboard or CLI without changing the engine's public API."""

    async def approve_candidate(
        self, *, run_id: uuid.UUID, candidate_id: uuid.UUID, approver: str
    ) -> None:
        ...

    async def reject_candidate(
        self, *, run_id: uuid.UUID, candidate_id: uuid.UUID, approver: str, reason: str
    ) -> None:
        ...


class ReflectionTrigger(Protocol):
    """Something that produces a mission-terminal event the engine
    listens to. Implemented by `InMemoryEventBus`'s `subscribe`
    mechanism -- the engine itself doesn't depend on the trigger, only
    on the EventBus, so no Protocol is needed here. This Protocol is
    reserved for a future adapter that surfaces reflection triggers
    from a non-event source (e.g. a backfill job re-running
    reflection on historical missions)."""

    async def listen(self) -> None:
        ...


class CandidateExtractor(Protocol):
    """The Phase-2 candidate-generation step's pluggable strategy.

    The default implementation (`DefaultCandidateExtractor` in
    `service.py`) inspects harvested log entries and memory entries
    for known signal patterns -- error-recovery sequences, tool-use
    repetitions, user-feedback events -- and emits a candidate per
    pattern. A future LLM-backed extractor could replace it without
    changing the engine.

    The Protocol deliberately returns a list of "raw candidate dicts"
    rather than fully-typed `ReflectionCandidate`s: the engine
    constructs the typed models from the dicts in Phase 3, after
    scoring has been computed. This keeps the extractor unaware of the
    scoring triple."""

    async def extract(
        self,
        *,
        mission_id: uuid.UUID,
        harvested: list[Any],
        read_only_context: list[Any],
    ) -> list[dict[str, Any]]:
        ...