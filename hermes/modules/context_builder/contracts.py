"""Protocols and inter-module contracts for the Context Builder.

The Context Builder depends only on the Memory read surface and the
Knowledge Graph runtime. Writing is intentionally not in either
Protocol -- the Context Builder is strictly an assembly operation,
not a memory promotion operation (that's the Reflection Engine's
job).

The split between `MemoryReader` (in `knowledge_graph/contracts.py`)
and a `KnowledgeGraph` Protocol here mirrors how the layers
consume each other: KG already declares its `MemoryReader`; the
Context Builder declares its own narrow `GraphReader` that the KG
satisfies structurally.
"""
from __future__ import annotations

import uuid
from typing import Any, Protocol

from hermes.modules.knowledge_graph.models import (
    ExpandedContext,
    InfluenceBreakdown,
    PropagatedConfidence,
)


class GraphReader(Protocol):
    """The subset of the Knowledge Graph the Context Builder consumes.

    The Builder calls `expansion` to fan out from a seed set, then
    `neighbourhood` for typed-edge traversal per seed, then
    `propagated_confidence` to weight scores from each entry back
    to the seed. It does not call `influence_score` -- that's a
    global-context measure not used for per-seed ranking.
    """

    async def neighbourhood(  # pragma: no cover - structural Protocol
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

    async def expansion(  # pragma: no cover - structural Protocol
        self,
        *,
        requesting_agent_id: str,
        seed_ids: list[uuid.UUID],
        max_hops: int = 1,
        limit: int | None = None,
    ) -> ExpandedContext:
        ...

    async def propagated_confidence(  # pragma: no cover - structural Protocol
        self,
        *,
        requesting_agent_id: str,
        from_id: uuid.UUID,
        to_id: uuid.UUID,
        max_hops: int = 4,
    ) -> PropagatedConfidence:
        ...


class ContextBuilderProtocol(Protocol):
    """The surface other modules (Reasoning Engine, Commander) consume.

    `assemble(...)` takes a `ContextRequest` and returns an
    `AssembledContext` (entries ordered by score, plus a per-entry
    scoring trace). The Builder is **idempotent**: calling `assemble`
    twice with the same request returns identical results -- there
    is no per-call state, just a deterministic computation over
    Memory + KG.
    """

    async def assemble(self, request: Any) -> Any:
        ...
