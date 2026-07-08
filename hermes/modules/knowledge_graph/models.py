"""Pydantic data models for the Knowledge Graph runtime layer.

The runtime layer is read-only over `MemoryEntry.relationships`,
`MemoryEntry.backlinks`, and `MemoryEntry.tags` -- it never writes
back to Memory Manager. Every model here is a *computed* return type,
not a persisted shape.
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from hermes.modules.memory_manager.models import MemoryEntry


ExpansionStrategy = Literal[
    "structural",  # 1-hop expansion over typed relationships + shared tags
    "hybrid",      # structural + tag-overlap; today's default
]


class Neighbour(BaseModel):
    """One entry reachable from a seed by `neighbourhood(...)`.

    `distance` is the minimum hop count (1 = direct neighbour, 2 =
    two hops away, etc.). `path_score` is the product of edge
    weights along the shortest typed path; clamped to [0.0, 1.0]
    by `_neighbourhood` in `service.py`. `path_edge_types` records
    the relationship types traversed so callers can audit why an
    entry surfaced as a neighbour.
    """

    entry: MemoryEntry
    distance: int = Field(ge=1)
    path_score: float = Field(ge=0.0, le=1.0)
    path_edge_types: list[str] = Field(default_factory=list)


class ExpandedContext(BaseModel):
    """The result of `KnowledgeGraph.expansion(...)`.

    `nodes` lists every entry surfaced by the expansion in score
    order (highest expansion score first). `score` is the
    structural+tag-overlap heuristic score the implementation
    computes (see `service.py`'s expansion scoring). `depth` is
    the hop distance of the expansion that produced this set
    (always 1 today -- deeper expansion would change shape).
    """

    seeds: list[uuid.UUID]
    nodes: list[Neighbour] = Field(default_factory=list)
    strategy: ExpansionStrategy = "hybrid"
    max_hops: int = 1


class InfluenceBreakdown(BaseModel):
    """The result of `KnowledgeGraph.influence_score(...)`.

    `score` is the clamped total. `weighted_contributions` lists
    each inbound edge that contributed, with its weight, the
    source entry's confidence, and the recency-decayed value the
    scorer folded into the total -- so callers can audit the
    score and reproduce it.
    """

    entry_id: uuid.UUID
    score: float = Field(ge=0.0, le=1.0)
    weighted_contributions: list[float] = Field(default_factory=list)
    inbound_edge_count: int = 0


class PropagatedConfidence(BaseModel):
    """The result of `KnowledgeGraph.propagated_confidence(...)`.

    `value` is the clamped product of `source.confidence` and
    each edge weight along the *shortest typed path* from source
    to target. `path` is the actual `GraphPath` used (empty if
    no typed path exists within `max_hops`). `hops` is `path.length`.
    """

    from_id: uuid.UUID
    to_id: uuid.UUID
    value: float = Field(default=0.0, ge=0.0, le=1.0)
    path: list[uuid.UUID] = Field(default_factory=list)
    hops: int = 0
    found: bool = False
