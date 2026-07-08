"""Protocols and inter-module contracts for the Knowledge Graph.

The Knowledge Graph runtime reads `MemoryEntry.relationships`,
`MemoryEntry.backlinks`, and `MemoryEntry.tags`. Every dependency is
declared here as a Protocol so the graph layer never imports
`MemoryManager` directly -- it consumes a narrow `MemoryReader`
Protocol a real Memory Manager satisfies structurally. A test fake
satisfies the same Protocol; the integration tests in
`hermes/tests/` exercise the graph against the real Memory Manager.
"""
from __future__ import annotations

import uuid
from typing import Any, Protocol, runtime_checkable

from hermes.modules.memory_manager.models import MemoryEntry
from hermes.modules.memory_manager.typed import MemoryRelationship


@runtime_checkable
class MemoryReader(Protocol):
    """The subset of Memory Manager the Knowledge Graph needs.

    `get` fetches one entry; `find_relationships` enumerates typed
    outbound/inbound edges; `get_backlinks` enumerates the looser
    reverse-edge links. The KG never writes -- no `record`,
    `record_typed`, `save`, `delete`, or `mark_superseded` method
    appears here. This shapes the boundary: a misconfigured
    collaborator can never gain write access by satisfying the
    Protocol.
    """

    async def get(self, *, requesting_agent_id: str, entry_id: uuid.UUID) -> MemoryEntry | None:
        ...

    async def find_relationships(
        self,
        *,
        requesting_agent_id: str,
        entry_id: uuid.UUID,
        relationship_type: str | None = None,
        direction: str = "outbound",
    ) -> list[MemoryRelationship]:
        ...

    async def get_backlinks(self, *, requesting_agent_id: str, entry_id: uuid.UUID) -> list[MemoryEntry]:
        ...

    async def query(
        self,
        *,
        requesting_agent_id: str,
        scope: str | None = None,
        tags: list[str] | None = None,
        owner_agent_id: str | None = None,
        session_id: str | None = None,
        workflow_run_id: uuid.UUID | None = None,
        memory_type: str | None = None,
        include_superseded: bool = False,
    ) -> list[MemoryEntry]:
        ...


class KnowledgeGraphProtocol(Protocol):
    """The surface other modules (Context Builder, Reasoning Engine,
    Commander) consume.

    Methods:

    - `neighbourhood(...)` -- entries reachable from a seed within
      `max_hops` along typed edges, ranked by `path_score`.
    - `expansion(...)` -- the seed's 1-hop structural+tag-overlap
      fan-out, ranked by expansion score.
    - `influence_score(...)` -- the clamped total of weight *
      confidence / (1 + age_in_days) over a candidate set's
      inbound edges to the target entry.
    - `propagated_confidence(...)` -- confidence * edge-weight
      product along the shortest typed path between two entries.

    All four methods are read-only over Memory. None of them
    raises on entries the requester can't read; unreadable
    entries are silently filtered, matching `MemoryManager`'s
    own `query()` semantics.
    """

    async def neighbourhood(
        self,
        *,
        requesting_agent_id: str,
        seed_id: uuid.UUID,
        max_hops: int = 2,
        min_confidence: float = 0.0,
        relationship_types: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Any]:
        ...

    async def expansion(
        self,
        *,
        requesting_agent_id: str,
        seed_ids: list[uuid.UUID],
        max_hops: int = 1,
        limit: int | None = None,
    ) -> Any:
        ...

    async def influence_score(
        self,
        *,
        requesting_agent_id: str,
        entry_id: uuid.UUID,
        candidate_set_ids: list[uuid.UUID],
    ) -> Any:
        ...

    async def propagated_confidence(
        self,
        *,
        requesting_agent_id: str,
        from_id: uuid.UUID,
        to_id: uuid.UUID,
        max_hops: int = 4,
    ) -> Any:
        ...
