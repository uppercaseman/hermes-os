"""Knowledge Graph-specific exception types.

The runtime layer is read-only over Memory, so failure modes are
narrow: an unknown seed, a malformed hop count, or a recomputed
graph cycle that BFS detected. Cycles can't actually appear in the
typed-edges substrate (since edges are typed and forward-only), but
the defensive `GraphCycleError` is reserved for a future
stateful-graph layer.
"""
from __future__ import annotations

import uuid


class KnowledgeGraphError(Exception):
    """Base class for all Knowledge Graph failures."""


class UnknownGraphNodeError(KnowledgeGraphError):
    """A caller referenced an entry id that the graph has no
    record of -- either it was never written, was deleted, or is
    in a Memory Manager permission boundary the caller can't see.
    """

    def __init__(self, entry_id: uuid.UUID) -> None:
        self.entry_id = entry_id
        super().__init__(f"no memory entry with id {entry_id}")


class GraphConfigError(KnowledgeGraphError):
    """The graph was constructed without a required collaborator
    (`memory`) or a method got invalid arguments (e.g. `max_hops=0`).
    Mirrors `ReflectionEngineConfigError`'s posture in
    `reflection_engine/errors.py`.
    """


class GraphCycleError(KnowledgeGraphError):
    """Defensive: BFS over a graph with no cycles can still detect
    one if a future stateful graph layer accidentally creates a
    bidirectional edge. Today the typed-edges substrate makes this
    unreachable, but the error is reserved.
    """
