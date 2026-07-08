# Knowledge Graph runtime

> **Sprint-3 — Knowledge & Reasoning Layer · ADR-0022 / Knowledge Graph spec**

The Knowledge Graph runtime is a **read-only** computation layer
over the typed substrate that Memory Manager already persists on
every `MemoryEntry`. It performs **traversal, neighbourhood
discovery, semantic relationship expansion, influence scoring, and
confidence propagation** without introducing a separate storage
engine — and without modifying Memory Manager, Reflection Engine,
or Commander.

The substrate is the union of three existing fields:

- `MemoryEntry.relationships` — first-class typed directed edges
  (Sprint-2 addition). The strongest structural signal.
- `MemoryEntry.backlinks` — untyped uuid reverse references; a
  looser "loose link" mechanism inherited from Sprint-1.
- `MemoryEntry.tags` — facet-style classification; doubles as the
  "shared-context" signal the expansion heuristic uses to surface
  related entries without typed edges.

The runtime reads these three fields, computes over them, and
returns typed Pydantic models (`Neighbour`, `ExpandedContext`,
`InfluenceBreakdown`, `PropagatedConfidence`). It never writes.

## The five algorithms

1. **`neighbourhood(seed_id, max_hops)`** — BFS over typed outbound
   edges from a seed. Returns one `Neighbour` per reachable entry,
   ranked by `path_score = product(edge weights along shortest
   path)`, clamped to `[0.0, 1.0]`. Optional `min_confidence`
   floor; optional `relationship_types` whitelist.
2. **`expansion(seed_ids, max_hops=1)`** — 1-hop structural +
   tag-overlap fan-out from a *set* of seeds. Score is

       typed_edge_score (1.0 per direct typed edge from any seed)
       + tag_overlap    (0.5 × |shared_tags| / |node_tags|)
       + backlink_score (0.3 per backlink from any seed)

   Today's "semantic" expansion is the structural-only heuristic —
   `Knowledge Graph.md`'s Future Considerations explicitly defer a
   real vector/semantic retrieval to a future sprint.
3. **`influence_score(entry_id, candidate_set_ids)`** —
   `Σ weight × source.confidence / (1 + age_in_days)` over inbound
   edges from `candidate_set_ids` to `entry_id`, clamped to
   `[0.0, 1.0]`. Influence is a *relative* measure (an entry's
   influence *within* the current context), not a global one.
4. **`propagated_confidence(from_id, to_id, max_hops)`** —
   `source.confidence × product(edge weights along best typed
   path)`, clamped to `[0.0, 1.0]`. Multi-hop propagation multiplies
   along the chain, attenuating with distance.
5. **Performance budget** — 10,000-edge BFS must complete in
   < 200 ms (per the spec). Verified by the integration test
   `test_neighbourhood_budget_under_200ms_for_10k_edges`.

## Public surface

```python
from hermes.modules.knowledge_graph import (
    # Runtime + factory
    KnowledgeGraph,
    KnowledgeGraphProtocol,
    build_knowledge_graph,
    # Cross-module contract
    MemoryReader,
    # Return models
    Neighbour,
    ExpandedContext,
    InfluenceBreakdown,
    PropagatedConfidence,
    # Errors
    KnowledgeGraphError,
    UnknownGraphNodeError,
    GraphConfigError,
    GraphCycleError,
    # Events
    KG_TRAVERSAL_PERFORMED,
    KG_EXPANSION_PERFORMED,
    KG_INFLUENCE_COMPUTED,
)

kg = build_knowledge_graph(memory=memory_manager, event_bus=bus)
neighbours = await kg.neighbourhood(
    requesting_agent_id="reflector",
    seed_id=skill_entry.id,
    max_hops=2,
    min_confidence=0.5,
    relationship_types=[MemoryRelationshipType.DERIVED_FROM],
    limit=10,
)
expanded = await kg.expansion(
    requesting_agent_id="reflector",
    seed_ids=[skill_entry.id, experience_entry.id],
)
influence = await kg.influence_score(
    requesting_agent_id="reflector",
    entry_id=skill_entry.id,
    candidate_set_ids=[e.id for e in neighbours],
)
propagated = await kg.propagated_confidence(
    requesting_agent_id="reflector",
    from_id=experience_entry.id,
    to_id=skill_entry.id,
    max_hops=4,
)
```

## Backwards compatibility

- **No Memory Manager change** — `MemoryManager`'s public surface
  (and the Sprint-2 typed additions) is unchanged. The KG consumes
  Memory Manager through the `MemoryReader` Protocol, which a real
  `MemoryManager` satisfies structurally.
- **No Reflection Engine change** — Sprint-2's `MemoryWriter`
  Protocol gains no new methods.
- **No Commander change** — Commander's `MemoryResolver` Protocol
  stays put; the binding helper lives in
  `reasoning_engine/interface.py` so Commander service internals
  are untouched.
- **No new persistence** — every KG computation is a query; the
  runtime stores no state beyond a transient BFS queue.

## Events

| Constant | Fires when |
| -------- | ---------- |
| `KG_TRAVERSAL_PERFORMED` | `neighbourhood` / `propagated_confidence` BFS completes |
| `KG_EXPANSION_PERFORMED` | `expansion` fan-out completes |
| `KG_INFLUENCE_COMPUTED`  | `influence_score` returns a result |

Publishing is best-effort — a bus failure does not fail the read-
only graph operation that produced the event.

## Folder structure

```
hermes/modules/knowledge_graph/
├── README.md
├── __init__.py
├── interface.py            <- KnowledgeGraph + factory + Protocol
├── service.py              <- KnowledgeGraph runtime
├── models.py               <- Neighbour, ExpandedContext, InfluenceBreakdown, PropagatedConfidence
├── contracts.py            <- MemoryReader, KnowledgeGraphProtocol
├── events.py               <- 3 event constants
├── errors.py               <- 4 exception types
└── tests/test_service.py   <- ~28 tests against real MemoryManager
```

## Out of scope (next sprint candidates)

- Real vector/semantic expansion (currently structural heuristic).
- Persistent graph indices (today everything is computed in-process).
- Cross-mission influence decay (today's recency decay is local to
  one candidate-set call, not a global graph-wide clock).