"""Public entry point for the Knowledge Graph.

Mirrors every other module's `interface.py`: import from here, never
from `service.py` directly. Re-exports the typed models, the
Protocol, the event constants, and the factory `build_knowledge_graph`.
"""
from __future__ import annotations

import uuid
from typing import Protocol

from hermes.core.event_bus.interface import EventBus
from hermes.modules.knowledge_graph.contracts import MemoryReader
from hermes.modules.knowledge_graph.models import (
    ExpandedContext,
    InfluenceBreakdown,
    PropagatedConfidence,
)
from hermes.modules.knowledge_graph.service import KnowledgeGraph

__all__ = [
    "KnowledgeGraph",
    "KnowledgeGraphProtocol",
    "build_knowledge_graph",
    "Neighbour",
    "ExpandedContext",
    "InfluenceBreakdown",
    "PropagatedConfidence",
    "MemoryReader",
]


# A runtime alias kept separate from `service.KnowledgeGraph` so
# callers can `from hermes.modules.knowledge_graph import
# KnowledgeGraph` and the abstract-Protocol typedef below isn't
# shadowed.
KnowledgeGraph = KnowledgeGraph


class KnowledgeGraphProtocol(Protocol):
    """Re-export of `contracts.KnowledgeGraphProtocol` at the public
    surface.

    The Protocol body lives in `contracts.py` so a reviewer can
    compare a real class against it without importing the
    implementation. This stub is here purely so
    `interface.py` matches the reflection_engine / memory_manager
    convention of declaring the Protocol at the boundary.
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
    ) -> list:
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

    async def influence_score(  # pragma: no cover - structural Protocol
        self,
        *,
        requesting_agent_id: str,
        entry_id: uuid.UUID,
        candidate_set_ids: list[uuid.UUID],
    ) -> InfluenceBreakdown:
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


# `Neighbour` is re-exported here from models.py via __init__.py;
# we don't import it again to keep the public surface explicit.


def build_knowledge_graph(
    *,
    memory: MemoryReader,
    event_bus: EventBus | None = None,
    agent_id: str = "knowledge_graph",
) -> KnowledgeGraph:
    """Factory mirroring the rest of the codebase. `memory` is required
    (the runtime has no useful default); `event_bus` and `agent_id`
    have sensible defaults.
    """
    return KnowledgeGraph(memory=memory, event_bus=event_bus, agent_id=agent_id)
