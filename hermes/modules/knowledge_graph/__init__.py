"""Knowledge Graph runtime layer.

A read-only computation layer over Memory Manager's typed
relationships, backlinks, and tags. **No separate storage engine** --
the substrate lives in `MemoryEntry.relationships` /
`backlinks` / `tags`; this module only computes over it.

Public surface mirrors every other `hermes/modules/` package:
import from here, never from `service.py` directly.

  >>> from hermes.modules.knowledge_graph import (
  ...     KnowledgeGraph,
  ...     KnowledgeGraphProtocol,
  ...     build_knowledge_graph,
  ...     Neighbour,
  ...     ExpandedContext,
  ...     InfluenceBreakdown,
  ...     PropagatedConfidence,
  ... )
"""
from hermes.modules.knowledge_graph import events as events_module
from hermes.modules.knowledge_graph.contracts import (
    KnowledgeGraphProtocol,
    MemoryReader,
)
from hermes.modules.knowledge_graph.errors import (
    GraphConfigError,
    GraphCycleError,
    KnowledgeGraphError,
    UnknownGraphNodeError,
)
from hermes.modules.knowledge_graph.interface import (
    KnowledgeGraph as _KnowledgeGraph,
    build_knowledge_graph,
)
from hermes.modules.knowledge_graph.models import (
    ExpandedContext,
    ExpansionStrategy,
    InfluenceBreakdown,
    Neighbour,
    PropagatedConfidence,
)
from hermes.modules.knowledge_graph.service import KnowledgeGraph

KG_EXPANSION_PERFORMED = events_module.KG_EXPANSION_PERFORMED
KG_INFLUENCE_COMPUTED = events_module.KG_INFLUENCE_COMPUTED
KG_TRAVERSAL_PERFORMED = events_module.KG_TRAVERSAL_PERFORMED

__all__ = [
    "KnowledgeGraph",
    "KnowledgeGraphProtocol",
    "MemoryReader",
    "build_knowledge_graph",
    "Neighbour",
    "ExpandedContext",
    "ExpansionStrategy",
    "InfluenceBreakdown",
    "PropagatedConfidence",
    "KnowledgeGraphError",
    "UnknownGraphNodeError",
    "GraphConfigError",
    "GraphCycleError",
    "KG_TRAVERSAL_PERFORMED",
    "KG_EXPANSION_PERFORMED",
    "KG_INFLUENCE_COMPUTED",
    "events_module",
]
