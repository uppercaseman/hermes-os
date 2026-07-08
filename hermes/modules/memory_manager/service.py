"""Memory Manager -- structured, permissioned, taggable memory.

Every named memory category from the spec maps onto one model
(`MemoryEntry`) and a small set of operations, rather than seven parallel
stores:

- Short-term conversation memory: `scope="session"`, `session_id` set,
  usually with a `ttl_seconds` so it expires.
- Long-term project memory: `scope="persistent"`.
- Agent memory: any scope with `owner_agent_id` set -- ownership and
  lifecycle scope are orthogonal, not the same axis.
- Workflow memory: `scope="workflow"`, `workflow_run_id` set.
- Decision / error history: `scope="decision"` / `scope="error"`,
  written only through `record_decision`/`record_error`, which always
  create a new entry -- `save()` refuses these two scopes outright,
  since a history log must never silently overwrite a prior entry the
  way a keyed `save()` upsert does for the other three scopes.
- Obsidian vault integration: `markdown.py` renders a real, tested
  Obsidian note (frontmatter + wiki-link backlinks) from any entry; an
  optional `MemoryBackend` (e.g. `ObsidianVaultAdapter`, a placeholder)
  is where that would actually get written to disk. A backend failure
  is never allowed to fail the save/delete that triggered it -- the
  in-process store is the source of truth, the backend is a best-effort
  sync target.

Permissions: an agent always has full access to memory it owns.
Read access to the shared (ownerless) pool is open by default. Anything
else -- writing to shared memory, or reading/writing another specific
agent's private memory -- requires an explicit `MemoryPermissionGrant`.

Sprint-2 (Cognitive Memory Architecture) typed extension: the
`MemoryEntry` model gains optional `memory_type`, `confidence`,
`importance`, `provenance`, `superseded_by`, and `relationships`
fields. `memory_type` is one of the six canonical cognitive
types (`user_dna`, `working_memory`, `mission_memory`,
`project_memory`, `skill_memory`, `experience_memory`) per
`Specification/02 - Cognitive Architecture/Memory Galaxy.md`. Two
new public surfaces are added:

- `record_typed(...)` -- the typed write path; mirrors `save(...)`
  but accepts and persists the typed fields.
- `mark_superseded(...)` -- the additive-only supersession
  primitive the Reflection Engine's `MemoryWriter` Protocol
  declares (Sprint-1 implementations called this against a fake;
  the real one is now wired here).

Knowledge Graph traversal uses the typed `relationships` field as
its first-class substrate. The existing `tags` and `backlinks`
fields continue to work unchanged.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.memory_manager import events as evt
from hermes.modules.memory_manager.contracts import MemoryBackend, VectorSearchProvider
from hermes.modules.memory_manager.errors import (
    MemoryPermissionDeniedError,
    UnknownMemoryEntryError,
    VectorSearchNotConfiguredError,
)
from hermes.modules.memory_manager.models import MemoryEntry, MemoryPermissionGrant, MemoryScope
from hermes.modules.memory_manager.typed import (
    ALL_MEMORY_TYPES,
    GraphPath,
    MemoryRelationship,
    MemoryRelationshipType,
    MemoryType,
    Provenance,
    REFLECTION_ENGINE_MANAGED_TAG,
    SUPERSEDED_TAG,
    all_memory_types,
    default_tags_for_memory_type,
    is_memory_type,
    tag_for_memory_type,
)

SOURCE_MODULE = "memory_manager"

_APPEND_ONLY_SCOPES = {"decision", "error"}
_KeyTuple = tuple[str, str | None, str | None, str | None, str]


class MemoryManager:
    def __init__(
        self,
        *,
        backend: MemoryBackend | None = None,
        vector_search: VectorSearchProvider | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._backend = backend
        self._vector_search = vector_search
        self._bus = event_bus
        self._entries: dict[uuid.UUID, MemoryEntry] = {}
        self._key_index: dict[_KeyTuple, uuid.UUID] = {}
        self._grants: list[MemoryPermissionGrant] = []
        # Sprint-2 typed indices -- populated lazily by `record_typed`
        # and `mark_superseded`; never read by the existing methods,
        # so they don't change existing test behaviour. The
        # `_memory_type_index` is `dict[memory_type_string,
        # set[entry_id]]` -- using the string (from the `Literal`)
        # directly keeps index lookups cheap.
        self._memory_type_index: dict[str, set[uuid.UUID]] = defaultdict(set)
        # Reverse edge of `_relationships`. Kept separately so a
        # future ADR that adds a different typed-edge index doesn't
        # have to retrofit backwards. The forward edge
        # (`relationships`) lives inside each `MemoryEntry`.
        self._relationship_index: dict[uuid.UUID, list[MemoryRelationship]] = {}

    # ------------------------------------------------------------------ #
    # Permissions
    # ------------------------------------------------------------------ #
    def grant_permission(
        self, agent_id: str, *, owner_agent_id: str | None = None, can_read: bool = True, can_write: bool = False
    ) -> None:
        """Grants `agent_id` access to memory owned by `owner_agent_id`
        (`None` = the shared pool). Replaces any existing grant for the
        same (agent_id, owner_agent_id) pair."""
        self._grants = [
            g for g in self._grants if not (g.agent_id == agent_id and g.owner_agent_id == owner_agent_id)
        ]
        self._grants.append(
            MemoryPermissionGrant(agent_id=agent_id, owner_agent_id=owner_agent_id, can_read=can_read, can_write=can_write)
        )

    def revoke_permission(self, agent_id: str, *, owner_agent_id: str | None = None) -> None:
        self._grants = [
            g for g in self._grants if not (g.agent_id == agent_id and g.owner_agent_id == owner_agent_id)
        ]

    def _has_grant(self, agent_id: str, owner_agent_id: str | None, permission: str) -> bool:
        for grant in self._grants:
            if grant.agent_id == agent_id and grant.owner_agent_id == owner_agent_id:
                return grant.can_read if permission == "read" else grant.can_write
        return False

    def _check_permission(self, entry: MemoryEntry, requesting_agent_id: str, permission: str) -> bool:
        if entry.owner_agent_id is None:
            return True  # shared memory: open read AND write by default
        if entry.owner_agent_id == requesting_agent_id:
            return True  # an agent always has full access to its own memory
        return self._has_grant(requesting_agent_id, entry.owner_agent_id, permission)

    # ------------------------------------------------------------------ #
    # Structured save / retrieve  (UNCHANGED signatures)
    # ------------------------------------------------------------------ #
    async def save(
        self,
        *,
        requesting_agent_id: str,
        scope: MemoryScope,
        key: str,
        value: dict[str, Any],
        owner_agent_id: str | None = None,
        session_id: str | None = None,
        workflow_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        backlinks: list[uuid.UUID] | None = None,
        ttl_seconds: float | None = None,
        memory_type: MemoryType | None = None,
        confidence: float | None = None,
        importance: float | None = None,
        provenance: list[Provenance] | None = None,
        superseded_by: uuid.UUID | None = None,
        relationships: list[MemoryRelationship] | None = None,
    ) -> MemoryEntry:
        """Saves a structured memory, upserting by (scope, owner,
        session/workflow, key) -- calling this again with the same
        combination replaces the value rather than creating a duplicate.

        Sprint-2 typed extension: every typed field is OPTIONAL and
        defaults to None / empty. Existing callers that don't pass
        any of the new fields keep their old behaviour exactly. New
        optional kwargs (`memory_type`, `confidence`, `importance`,
        `provenance`, `superseded_by`, `relationships`) thread
        through to the constructed `MemoryEntry` for callers that
        want first-class typed writes without using `record_typed`.

        Raises `ValueError` for `scope in ("decision", "error")`;
        use `record_decision`/`record_error` for those, which
        always append.
        """
        if scope in _APPEND_ONLY_SCOPES:
            raise ValueError(f"scope {scope!r} is append-only; use record_decision/record_error instead")
        self._validate_typed_fields(memory_type=memory_type, confidence=confidence, importance=importance)

        # Shared memory (owner_agent_id=None) is writable by default, same
        # as it's readable by default -- only writing into ANOTHER agent's
        # private memory requires an explicit grant.
        if owner_agent_id is not None and owner_agent_id != requesting_agent_id:
            if not self._has_grant(requesting_agent_id, owner_agent_id, "write"):
                raise MemoryPermissionDeniedError(requesting_agent_id, owner_agent_id, "write")

        composite_key: _KeyTuple = (
            scope,
            owner_agent_id,
            session_id,
            str(workflow_run_id) if workflow_run_id else None,
            key,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds) if ttl_seconds is not None else None
        existing_id = self._key_index.get(composite_key)

        entry = MemoryEntry(
            id=existing_id or uuid.uuid4(),
            scope=scope,
            owner_agent_id=owner_agent_id,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            key=key,
            value=value,
            tags=tags or [],
            backlinks=backlinks or [],
            expires_at=expires_at,
            memory_type=memory_type,
            confidence=confidence,
            importance=importance,
            provenance=list(provenance or []),
            superseded_by=superseded_by,
            relationships=list(relationships or []),
            created_at=self._entries[existing_id].created_at if existing_id else datetime.now(timezone.utc),
        )
        # If this is an upsert overwriting an existing entry, drop
        # the old id from the typed indices before adding the new
        # one (the index is keyed by entry id, not by composite
        # upsert key).
        if existing_id is not None:
            self._remove_from_typed_indices(self._entries[existing_id])
        self._add_to_typed_indices(entry)

        self._key_index[composite_key] = entry.id
        self._entries[entry.id] = entry
        await self._sync_to_backend(entry, deleting=False)
        await self._publish(evt.ENTRY_SAVED, {"entry_id": str(entry.id), "scope": entry.scope, "memory_type": entry.memory_type})
        return entry

    async def get(self, *, requesting_agent_id: str, entry_id: uuid.UUID) -> MemoryEntry | None:
        entry = self._entries.get(entry_id)
        if entry is None or self._is_expired(entry):
            return None
        if not self._check_permission(entry, requesting_agent_id, "read"):
            raise MemoryPermissionDeniedError(requesting_agent_id, entry.owner_agent_id, "read")
        return entry

    async def get_by_key(
        self,
        *,
        requesting_agent_id: str,
        scope: MemoryScope,
        key: str,
        owner_agent_id: str | None = None,
        session_id: str | None = None,
        workflow_run_id: uuid.UUID | None = None,
    ) -> MemoryEntry | None:
        composite_key: _KeyTuple = (
            scope,
            owner_agent_id,
            session_id,
            str(workflow_run_id) if workflow_run_id else None,
            key,
        )
        entry_id = self._key_index.get(composite_key)
        if entry_id is None:
            return None
        return await self.get(requesting_agent_id=requesting_agent_id, entry_id=entry_id)

    async def query(
        self,
        *,
        requesting_agent_id: str,
        scope: MemoryScope | None = None,
        tags: list[str] | None = None,
        owner_agent_id: str | None = None,
        session_id: str | None = None,
        workflow_run_id: uuid.UUID | None = None,
        memory_type: MemoryType | None = None,
        include_superseded: bool = False,
    ) -> list[MemoryEntry]:
        """Every filter is AND-ed together; `tags` requires the entry to
        have ALL listed tags. Entries the requester can't read, or that
        have expired, are silently excluded -- never raised.

        Sprint-2 typed extension: `memory_type=...` filters by
        cognitive type; `include_superseded=False` (the default)
        hides entries that have been superseded, matching the
        Memory Galaxy "additive, never destructive" rule -- the
        underlying entries are still present, just not in the
        default view."""
        results = []
        for entry in self._entries.values():
            if self._is_expired(entry):
                continue
            if not include_superseded and entry.superseded_by is not None:
                continue
            if scope is not None and entry.scope != scope:
                continue
            if owner_agent_id is not None and entry.owner_agent_id != owner_agent_id:
                continue
            if session_id is not None and entry.session_id != session_id:
                continue
            if workflow_run_id is not None and entry.workflow_run_id != workflow_run_id:
                continue
            if memory_type is not None and entry.memory_type != memory_type:
                continue
            if tags and not set(tags).issubset(entry.tags):
                continue
            if not self._check_permission(entry, requesting_agent_id, "read"):
                continue
            results.append(entry)
        return results

    async def delete(self, *, requesting_agent_id: str, entry_id: uuid.UUID) -> None:
        entry = self._entries.get(entry_id)
        if entry is None:
            raise UnknownMemoryEntryError(entry_id)
        if not self._check_permission(entry, requesting_agent_id, "write"):
            raise MemoryPermissionDeniedError(requesting_agent_id, entry.owner_agent_id, "write")
        self._remove_from_typed_indices(entry)
        del self._entries[entry_id]
        for stale_key in [k for k, v in self._key_index.items() if v == entry_id]:
            del self._key_index[stale_key]
        if entry_id in self._relationship_index:
            del self._relationship_index[entry_id]
        await self._sync_to_backend(entry, deleting=True)
        await self._publish(evt.ENTRY_DELETED, {"entry_id": str(entry_id), "scope": entry.scope})

    # ------------------------------------------------------------------ #
    # Backlinks (Obsidian-style bidirectional references)
    # ------------------------------------------------------------------ #
    async def get_backlinks(self, *, requesting_agent_id: str, entry_id: uuid.UUID) -> list[MemoryEntry]:
        """Every entry (visible to `requesting_agent_id`) whose
        `backlinks` list includes `entry_id` -- the reverse direction of
        a link, exactly like Obsidian's backlinks panel."""
        return [
            entry
            for entry in self._entries.values()
            if entry_id in entry.backlinks
            and not self._is_expired(entry)
            and self._check_permission(entry, requesting_agent_id, "read")
        ]

    # ------------------------------------------------------------------ #
    # Decision / error history -- append-only
    # ------------------------------------------------------------------ #
    async def record_decision(
        self,
        *,
        agent_id: str,
        summary: str,
        details: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        backlinks: list[uuid.UUID] | None = None,
    ) -> MemoryEntry:
        return await self._record_history("decision", evt.DECISION_RECORDED, agent_id, summary, details, tags, backlinks)

    async def record_error(
        self,
        *,
        agent_id: str,
        summary: str,
        details: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        backlinks: list[uuid.UUID] | None = None,
    ) -> MemoryEntry:
        return await self._record_history("error", evt.ERROR_RECORDED, agent_id, summary, details, tags, backlinks)

    async def _record_history(
        self,
        scope: MemoryScope,
        event_type: str,
        agent_id: str,
        summary: str,
        details: dict[str, Any] | None,
        tags: list[str] | None,
        backlinks: list[uuid.UUID] | None,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            scope=scope,
            owner_agent_id=agent_id,
            key=summary,
            value=details or {},
            tags=tags or [],
            backlinks=backlinks or [],
        )
        self._entries[entry.id] = entry  # deliberately not added to _key_index -- always append, never upsert
        self._add_to_typed_indices(entry)
        await self._sync_to_backend(entry, deleting=False)
        await self._publish(event_type, {"entry_id": str(entry.id), "agent_id": agent_id})
        return entry

    async def get_decision_history(
        self, *, requesting_agent_id: str, owner_agent_id: str | None = None, limit: int | None = None
    ) -> list[MemoryEntry]:
        return await self._history("decision", requesting_agent_id, owner_agent_id, limit)

    async def get_error_history(
        self, *, requesting_agent_id: str, owner_agent_id: str | None = None, limit: int | None = None
    ) -> list[MemoryEntry]:
        return await self._history("error", requesting_agent_id, owner_agent_id, limit)

    async def _history(
        self, scope: MemoryScope, requesting_agent_id: str, owner_agent_id: str | None, limit: int | None
    ) -> list[MemoryEntry]:
        results = await self.query(requesting_agent_id=requesting_agent_id, scope=scope, owner_agent_id=owner_agent_id)
        results.sort(key=lambda e: e.created_at)
        return results[-limit:] if limit else results

    # ------------------------------------------------------------------ #
    # Future vector search
    # ------------------------------------------------------------------ #
    async def search_similar(
        self, *, requesting_agent_id: str, query_text: str, top_k: int = 5
    ) -> list[MemoryEntry]:
        """Delegates to the configured `VectorSearchProvider`. Raises
        `VectorSearchNotConfiguredError` if none was given -- this never
        silently returns an empty result for a capability that doesn't
        exist yet."""
        if self._vector_search is None:
            raise VectorSearchNotConfiguredError()
        embedding = await self._vector_search.embed(query_text)
        hits = await self._vector_search.search(embedding, top_k=top_k)
        results = []
        for entry_id, _score in hits:
            entry = self._entries.get(entry_id)
            if entry and not self._is_expired(entry) and self._check_permission(entry, requesting_agent_id, "read"):
                results.append(entry)
        return results

    # ------------------------------------------------------------------ #
    # Housekeeping
    # ------------------------------------------------------------------ #
    async def sweep_expired(self) -> int:
        """Reclaims expired entries. Reads never need this to be correct
        -- `get`/`get_by_key`/`query` already filter expired entries out
        lazily -- this only frees memory."""
        expired = [entry_id for entry_id, entry in self._entries.items() if self._is_expired(entry)]
        for entry_id in expired:
            entry = self._entries[entry_id]
            self._remove_from_typed_indices(entry)
            del self._entries[entry_id]
            for stale_key in [k for k, v in self._key_index.items() if v == entry_id]:
                del self._key_index[stale_key]
        return len(expired)

    # ====================================================================== #
    # Sprint-2 typed surfaces
    # ====================================================================== #

    # ------------------------------------------------------------------ #
    # record_typed -- the typed write path
    # ------------------------------------------------------------------ #
    async def record_typed(
        self,
        *,
        requesting_agent_id: str,
        memory_type: MemoryType,
        key: str,
        value: dict[str, Any],
        scope: MemoryScope | None = None,
        confidence: float | None = None,
        importance: float | None = None,
        provenance: list[Provenance] | None = None,
        relationships: list[MemoryRelationship] | None = None,
        owner_agent_id: str | None = None,
        session_id: str | None = None,
        workflow_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        backlinks: list[uuid.UUID] | None = None,
        ttl_seconds: float | None = None,
        origin_mission_id: uuid.UUID | None = None,
    ) -> MemoryEntry:
        """Writes a typed cognitive memory entry. Mirrors `save(...)`
        semantically (upsert by composite key) but routes through the
        typed fields and stamps a default tag set including
        `memory:<memory_type>` and `reflection_engine:managed`, so
        legacy tag-filter queries can still find the entry while
        the canonical store is the typed fields.

        `scope` defaults to `persistent` for non-`working_memory`
        types and `session` for `working_memory` -- the choice
        mirrors the spec ("Working Memory is session-scoped"; the
        other five types are persistent). Passing `scope` overrides
        the default.

        Per ADR-0006 / `Memory Galaxy`: the entry's durable record
        is the typed `value` dict plus provenance and confidence.
        Promotion by `record_typed(...)` does NOT carry the
        `superseded_by` field; use `mark_superseded(...)` for that.
        """
        if not is_memory_type(memory_type):
            raise ValueError(f"memory_type must be one of {ALL_MEMORY_TYPES}; got {memory_type!r}")
        self._validate_typed_fields(memory_type=memory_type, confidence=confidence, importance=importance)

        resolved_scope = scope or self._default_scope_for(memory_type)
        default_tags = default_tags_for_memory_type(memory_type, origin_mission_id=origin_mission_id)
        merged_tags = list(default_tags)
        if tags:
            for t in tags:
                if t not in merged_tags:
                    merged_tags.append(t)

        # Delegate to `save(...)` -- the optional typed kwargs thread
        # through, so this method is the documented shape and
        # `save(...)` is the implementation. That keeps the typed
        # path forward-compatible with any future `save(...)`
        # refinement.
        entry = await self.save(
            requesting_agent_id=requesting_agent_id,
            scope=resolved_scope,
            key=key,
            value=value,
            owner_agent_id=owner_agent_id,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            tags=merged_tags,
            backlinks=backlinks,
            ttl_seconds=ttl_seconds,
            memory_type=memory_type,
            confidence=confidence,
            importance=importance,
            provenance=provenance,
            relationships=relationships,
        )
        await self._publish(
            evt.ENTRY_TYPED_RECORDED,
            {
                "entry_id": str(entry.id),
                "memory_type": memory_type,
                "scope": resolved_scope,
            },
        )
        return entry

    # ------------------------------------------------------------------ #
    # mark_superseded -- additive-only supersession
    # ------------------------------------------------------------------ #
    async def mark_superseded(
        self,
        *,
        requesting_agent_id: str,
        entry_id: uuid.UUID,
        superseded_by: uuid.UUID,
    ) -> None:
        """Sets `entry.superseded_by = superseded_by` and tags the
        entry `superseded`. Additive only -- the old entry is
        never deleted; it's hidden from default `query()` results
        unless the caller passes `include_superseded=True`.

        The old-vs-new entries are independent: this method
        doesn't touch `entry.superseded_by`'s entry. The caller
        (typically the Reflection Engine) chooses which entry
        wins; this method just records the outcome.

        Idempotent on repeat calls with the same `superseded_by`:
        no duplicate events are published for the same replacement.
        """
        entry = self._entries.get(entry_id)
        if entry is None:
            raise UnknownMemoryEntryError(entry_id)
        if not self._check_permission(entry, requesting_agent_id, "write"):
            raise MemoryPermissionDeniedError(requesting_agent_id, entry.owner_agent_id, "write")
        if entry.id == superseded_by:
            raise ValueError("entry cannot supersede itself")
        if entry.superseded_by == superseded_by:
            # Idempotent re-application -- already marked, nothing to do.
            return

        entry.superseded_by = superseded_by
        if SUPERSEDED_TAG not in entry.tags:
            entry.tags.append(SUPERSEDED_TAG)
        # Bumping `created_at`-adjacent metadata is intentionally
        # NOT done -- the original timestamp is preserved (the
        # additive-only rule includes timestamps).
        await self._publish(
            evt.ENTRY_SUPERSEDED,
            {
                "entry_id": str(entry.id),
                "superseded_by": str(superseded_by),
                "memory_type": entry.memory_type,
            },
        )

    # ------------------------------------------------------------------ #
    # graph traversal (Knowledge Graph substrate)
    # ------------------------------------------------------------------ #
    async def find_relationships(
        self,
        *,
        requesting_agent_id: str,
        entry_id: uuid.UUID,
        relationship_type: str | None = None,
        direction: str = "outbound",
    ) -> list[MemoryRelationship]:
        """Returns the typed relationships of `entry_id`,
        optionally filtered by `relationship_type` and `direction`
        ("outbound" = edges where `entry_id` is the source;
        "inbound" = edges where `entry_id` is the target; "both" =
        union). Empty list for unknown entries or unreadable
        permission cases (the latter are silenced the same way
        `query()` silences them, never raised).

        Sorts by `weight` descending so the strongest edge comes
        first, matching the spec's pending design weight of typed
        edges. Unknown `relationship_type` strings produce an
        empty result rather than an error -- the open-ended
        substrate accepts future ADRs without code change here.
        """
        entry = self._entries.get(entry_id)
        if entry is None:
            return []
        if not self._check_permission(entry, requesting_agent_id, "read"):
            return []
        outbound = list(entry.relationships)
        if relationship_type is not None:
            outbound = [r for r in outbound if r.relationship_type == relationship_type]
        outbound.sort(key=lambda r: r.weight, reverse=True)

        inbound: list[MemoryRelationship] = []
        if direction in ("inbound", "both"):
            # Scan for entries whose `relationships` reference
            # `entry_id`. O(N) per call -- the indexed forward edge
            # lives inside each entry; the reverse is computed by
            # scan because storing it twice would break the
            # additive-only rule.
            for candidate_id, candidate in self._entries.items():
                if candidate_id == entry_id:
                    continue
                if self._is_expired(candidate):
                    continue
                if not self._check_permission(candidate, requesting_agent_id, "read"):
                    continue
                reverse = [
                    MemoryRelationship(
                        relationship_type=r.relationship_type,
                        target_entry_id=entry_id,
                        weight=r.weight,
                        description=r.description,
                    )
                    for r in candidate.relationships
                    if r.target_entry_id == entry_id
                    and (relationship_type is None or r.relationship_type == relationship_type)
                ]
                inbound.extend(reverse)
            inbound.sort(key=lambda r: r.weight, reverse=True)

        if direction == "outbound":
            return outbound
        if direction == "inbound":
            return inbound
        if direction == "both":
            return outbound + inbound
        raise ValueError(f"direction must be 'outbound', 'inbound', or 'both'; got {direction!r}")

    async def find_path(
        self,
        *,
        requesting_agent_id: str,
        from_id: uuid.UUID,
        to_id: uuid.UUID,
        max_depth: int = 6,
    ) -> GraphPath:
        """Returns the shortest typed path from `from_id` to `to_id`
        using BFS over the typed `relationships` subgraph, up to
        `max_depth` edges. An empty `GraphPath` (`length=0`) means
        "no path found within `max_depth`". The path follows
        outbound edges only -- a future helper could add
        direction-aware variants without API change.

        Permissions are silently enforced entry-by-entry: an entry
        the requester can't read is skipped, just like in
        `query(...)`. This means a path may have hidden vertices,
        but the visible ones form a valid typed path -- which is
        exactly what `Memory Galaxy`'s permission model intends.
        """
        if from_id == to_id:
            return GraphPath(nodes=[from_id], edges=[], length=0)

        visited: set[uuid.UUID] = {from_id}
        # Each queue entry is (current_node, path_of_nodes, path_of_edges).
        frontier: list[tuple[uuid.UUID, list[uuid.UUID], list[str]]] = [(from_id, [from_id], [])]
        for _ in range(max_depth):
            next_frontier: list[tuple[uuid.UUID, list[uuid.UUID], list[str]]] = []
            for current, node_path, edge_path in frontier:
                entry = self._entries.get(current)
                if entry is None:
                    continue
                if not self._check_permission(entry, requesting_agent_id, "read"):
                    continue
                for rel in entry.relationships:
                    if rel.target_entry_id in visited:
                        continue
                    target = self._entries.get(rel.target_entry_id)
                    if target is None or self._is_expired(target):
                        continue
                    if not self._check_permission(target, requesting_agent_id, "read"):
                        continue
                    new_nodes = node_path + [rel.target_entry_id]
                    new_edges = edge_path + [rel.relationship_type]
                    if rel.target_entry_id == to_id:
                        return GraphPath(nodes=new_nodes, edges=new_edges, length=len(new_edges))
                    visited.add(rel.target_entry_id)
                    next_frontier.append((rel.target_entry_id, new_nodes, new_edges))
            if not next_frontier:
                break
            frontier = next_frontier
        return GraphPath()

    # ====================================================================== #
    # Sprint-2 typed helpers
    # ====================================================================== #

    def _validate_typed_fields(
        self,
        *,
        memory_type: str | None,
        confidence: float | None,
        importance: float | None,
    ) -> None:
        """Centralised validator for typed-field inputs. Caught here
        -- not at every call site -- so the contract is uniform and
        typed-write APIs don't silently accept garbage."""
        if memory_type is not None and not is_memory_type(memory_type):
            raise ValueError(f"memory_type must be one of {ALL_MEMORY_TYPES}; got {memory_type!r}")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0]; got {confidence}")
        if importance is not None and not 0.0 <= importance <= 1.0:
            raise ValueError(f"importance must be in [0.0, 1.0]; got {importance}")

    def _default_scope_for(self, memory_type: MemoryType) -> MemoryScope:
        """The default `scope` for a typed write. Working Memory is
        session-scoped per the spec ("Working Memory is session-
        scoped"); the other five types persist, so the default is
        `persistent`. Caller can override via the `scope=` kwarg.
        """
        if memory_type == "working_memory":
            return "session"
        return "persistent"

    def _add_to_typed_indices(self, entry: MemoryEntry) -> None:
        if entry.memory_type is not None:
            self._memory_type_index[entry.memory_type].add(entry.id)
        for rel in entry.relationships:
            self._relationship_index.setdefault(rel.target_entry_id, [])

    def _remove_from_typed_indices(self, entry: MemoryEntry) -> None:
        if entry.memory_type is not None and entry.id in self._memory_type_index.get(entry.memory_type, set()):
            self._memory_type_index[entry.memory_type].discard(entry.id)
        # Reverse edges are computed on demand; nothing to remove.

    # ====================================================================== #
    # Helpers (unchanged)
    # ====================================================================== #

    def _is_expired(self, entry: MemoryEntry) -> bool:
        return entry.expires_at is not None and entry.expires_at <= datetime.now(timezone.utc)

    async def _sync_to_backend(self, entry: MemoryEntry, *, deleting: bool) -> None:
        if self._backend is None:
            return
        try:
            if deleting:
                await self._backend.delete_entry(entry.id)
            else:
                await self._backend.write_entry(entry)
        except Exception as exc:  # noqa: BLE001 -- the backend is a best-effort sync
            # target; the in-process store is the source of truth, so a
            # backend failure (e.g. a placeholder adapter) must never
            # fail the save/delete that triggered it.
            await self._publish(evt.BACKEND_SYNC_FAILED, {"entry_id": str(entry.id), "error": str(exc)})

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(event_type=event_type, source_module=SOURCE_MODULE, correlation_id=uuid.uuid4(), payload=payload)
        )
