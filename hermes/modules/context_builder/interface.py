"""Public entry point for the Context Builder.

Mirrors every other module's `interface.py`: import from here, never
from `service.py` directly. Re-exports the typed models, the
Protocol, the event constants, and the factory `build_context_builder`.

The factory accepts either a real `KnowledgeGraph` (a structural
subclass of `GraphReader`) or any object satisfying the Protocol --
so test code can pass a hand-rolled stub.
"""
from __future__ import annotations

from typing import Protocol

from hermes.core.event_bus.interface import EventBus
from hermes.modules.context_builder.contracts import GraphReader
from hermes.modules.context_builder.models import (
    AssembledContext,
    ContextRequest,
    ContextScoreEntry,
)
from hermes.modules.context_builder.service import ContextBuilder
from hermes.modules.knowledge_graph.contracts import MemoryReader
from hermes.modules.knowledge_graph.service import KnowledgeGraph  # noqa: F401  -- re-export

__all__ = [
    "ContextBuilder",
    "ContextBuilderProtocol",
    "GraphReader",
    "MemoryReader",
    "KnowledgeGraph",
    "build_context_builder",
    "ContextRequest",
    "AssembledContext",
    "ContextScoreEntry",
]


class ContextBuilderProtocol(Protocol):
    """Re-export of `contracts.ContextBuilderProtocol` at the public surface.

    The Protocol body lives in `contracts.py` so a reviewer can
    compare a real class against it without importing the implementation.
    """

    async def assemble(  # pragma: no cover - structural Protocol
        self, request: ContextRequest
    ) -> AssembledContext:
        ...


def build_context_builder(
    *,
    memory: MemoryReader,
    kg: GraphReader,
    event_bus: EventBus | None = None,
    agent_id: str = "context_builder",
) -> ContextBuilder:
    """Factory mirroring the rest of the codebase. `memory` and `kg`
    are both required. A real `KnowledgeGraph` instance satisfies
    `GraphReader` structurally; a test stub satisfies it by
    duck-typing the four declared methods.
    """
    return ContextBuilder(memory=memory, kg=kg, event_bus=event_bus, agent_id=agent_id)
