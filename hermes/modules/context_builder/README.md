# Context Builder

> **Sprint-3 — Knowledge & Reasoning Layer**

The Context Builder assembles the **most relevant memories** for
any mission or reasoning request by combining Knowledge Graph
traversal, expansion, and confidence propagation.

It is the assembly layer the Reasoning Engine consumes to prepare
its structured `ReasoningContext`. Read-only over Memory — the
single writer to cognitive memory remains the Reflection Engine.

## Inputs and outputs

```python
from hermes.modules.context_builder import (
    ContextBuilder,
    build_context_builder,
    ContextRequest,
    AssembledContext,
    ContextScoreEntry,
)

cb = build_context_builder(memory=memory_manager, kg=knowledge_graph)
ctx = await cb.assemble(ContextRequest(
    requesting_agent_id="commander",
    seed_ids=[skill_entry.id, experience_entry.id],
    mission_id=uuid.uuid4(),
    k=8,
    min_confidence=0.4,
    max_hops=2,
))
# ctx.entries -- MemoryEntry list ordered by score descending
# ctx.scoring_trace -- one ContextScoreEntry per result (audit trail)
# ctx.metadata -- request parameters + counts
```

## The scoring heuristic

The Builder combines four signals per candidate:

| Signal                            | Weight |
| --------------------------------- | ------ |
| Average propagated confidence from every seed to the candidate | 0.5 |
| Path score from Knowledge Graph traversal | 0.3 |
| Candidate's own typed `confidence` | 0.2 |

The total is clamped to `[0.0, 1.0]`. The weights are module-internal
constants today; a future ADR that wants to retune them would do so
in `service.py`.

Seeds are scored as their own entries — a seed is, by definition,
fully relevant. Their `path_score` is 1.0 and the entry's own
confidence is folded in via the same weighted sum.

## Assembly pipeline

1. **Resolve seeds** — fetch each seed, silently skip unreadable /
   missing ones.
2. **Per-seed neighbourhood** — BFS over typed outbound edges from
   each seed at `max_hops`.
3. **Expansion** — structural + tag-overlap fan-out from the seed
   set (one call to `KnowledgeGraph.expansion`).
4. **Per-entry scoring** — propagate confidence from each seed to
   each candidate, weight against the path score and entry
   confidence.
5. **Cap to `k`** — keep the top-`k` entries by score.

## Backwards compatibility

- No Memory Manager change — `MemoryReader` Protocol is a subset of
  `MemoryManager`'s public surface.
- No Knowledge Graph change — `GraphReader` Protocol is a subset of
  `KnowledgeGraph`'s public surface.
- No Commander change — Commander's `MemoryResolver` stays put; the
  Binding helper lives in `reasoning_engine/interface.py`.

## Events

| Constant | Fires when |
| -------- | ---------- |
| `CONTEXT_BUILT` | An `AssembledContext` has been returned successfully |
| `CONTEXT_BUILD_FAILED` | Assembly could not produce a non-empty result |

Publishing is best-effort — a bus failure does not fail the read-
only assembly that produced the event.

## Folder structure

```
hermes/modules/context_builder/
├── README.md
├── __init__.py
├── interface.py            <- ContextBuilder + factory + Protocol
├── service.py              <- ContextBuilder runtime
├── models.py               <- ContextRequest, AssembledContext, ContextScoreEntry
├── contracts.py            <- GraphReader, ContextBuilderProtocol
├── events.py               <- 2 event constants
├── errors.py               <- 3 exception types
└── tests/test_service.py   <- ~30 tests against real MemoryManager + KG
```

## Out of scope (next sprint candidates)

- A configurable scoring-weights surface (today's weights are
  module-internal constants; a future Pydantic config would let a
  caller retune them without an ADR).
- Persistent cache of recent `AssembledContext`s for backfill
  contexts (today every `assemble` is a fresh computation).
- A "relevance feedback" loop that updates weights based on which
  assembled entries downstream consumers actually used.