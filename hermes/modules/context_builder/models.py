"""Pydantic data models for the Context Builder.

The Context Builder's role is "assemble the most relevant memories
for any mission or reasoning request." Its primary return type is
`AssembledContext` -- an ordered list of `MemoryEntry`s with a
trace of how each entry was scored, so downstream consumers (the
Reasoning Engine, future dashboards) can audit and reproduce the
assembly.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from hermes.modules.memory_manager.models import MemoryEntry


ScoringMethod = Literal[
    "direct_seed",  # the entry is itself one of the seeds
    "neighbour",  # surfaced by Knowledge Graph traversal
    "expansion",  # surfaced by tag-overlap / backlink expansion
    "tag_match",  # surfaced by tag match only (rare)
]


class ContextRequest(BaseModel):
    """The inputs to `ContextBuilder.assemble(...)`.

    `seed_ids` is the canonical "starting set" the assembly expands
    from. `k` caps the assembled result to the top-k entries by
    `score`. `min_confidence` filters out entries whose typed
    `confidence` is below the floor (legacy entries default to 0.5
    the same way the Knowledge Graph does).
    """

    requesting_agent_id: str
    seed_ids: list[uuid.UUID]
    mission_id: uuid.UUID | None = None
    k: int = 8
    min_confidence: float = 0.0
    max_hops: int = 2
    include_superseded: bool = False


class ContextScoreEntry(BaseModel):
    """Per-entry scoring trace.

    Each `AssembledContext.entry` has one matching `ContextScoreEntry`
    carrying the score, the method that surfaced it, the path_score
    from the Knowledge Graph (if any), and the propagated confidence
    used to weight the final score.
    """

    entry_id: uuid.UUID
    score: float = Field(ge=0.0, le=1.0)
    method: ScoringMethod
    distance: int = 0  # 0 = seed; 1+ = KG traversal hops
    propagated_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    path_score: float = Field(default=0.0, ge=0.0, le=1.0)


class AssembledContext(BaseModel):
    """The Context Builder's primary output.

    `entries` is ordered by score descending -- the first entry is
    the most relevant according to the assembly heuristic.
    `scoring_trace` carries one `ContextScoreEntry` per result so
    downstream consumers can audit ordering decisions.
    `assembled_at` is when the snapshot was taken; `metadata`
    carries the request parameters for traceability.
    """

    request: ContextRequest
    entries: list[MemoryEntry] = Field(default_factory=list)
    scoring_trace: list[ContextScoreEntry] = Field(default_factory=list)
    assembled_at: datetime
    metadata: dict[str, str] = Field(default_factory=dict)
