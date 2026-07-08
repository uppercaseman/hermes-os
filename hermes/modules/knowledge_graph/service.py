"""Knowledge Graph runtime layer over Memory Manager.

The runtime reads `MemoryEntry.relationships`, `backlinks`, and `tags`
and computes over them in-process. **No separate storage engine is
introduced** -- this module stores nothing of its own beyond a
transient BFS queue.

Algorithmic core:

- **Neighbourhood**: BFS over the typed `relationships` subgraph
  from a seed, pruning edges below `min_confidence` and edges of
  the wrong type. Returns the shortest-path `Neighbour` per node.
- **Expansion**: 1-hop structural fan-out + tag-overlap scoring.
  Today's "semantic" expansion is heuristic -- the spec's
  `Knowledge Graph.md` Future Considerations explicitly defer
  vector/semantic retrieval, so this is the structural-only
  implementation.
- **Influence score**: weight * confidence / (1 + age_in_days) over
  every inbound edge from a candidate set, clamped to [0.0, 1.0].
- **Propagated confidence**: confidence of source * product of edge
  weights along the shortest typed path, clamped to [0.0, 1.0].
  Multi-hop propagation multiplies along the chain, so the value
  attenuates with distance.

Performance budget (per `Knowledge Graph.md`): 10,000-edge BFS
must complete in < 200ms. The neighbourhood BFS shares this
budget with `MemoryManager.find_path`, which already passes.

Permissions are silently enforced per-entry: entries the requester
can't read are skipped (matching `MemoryManager.query()` semantics).
"""
from __future__ import annotations

import math
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.knowledge_graph import events as kg_events
from hermes.modules.knowledge_graph.contracts import MemoryReader
from hermes.modules.knowledge_graph.errors import (
    GraphConfigError,
    KnowledgeGraphError,
    UnknownGraphNodeError,
)
from hermes.modules.knowledge_graph.models import (
    ExpandedContext,
    ExpansionStrategy,
    InfluenceBreakdown,
    Neighbour,
    PropagatedConfidence,
)
from hermes.modules.memory_manager.models import MemoryEntry
from hermes.modules.memory_manager.typed import MemoryRelationship, MemoryRelationshipType

SOURCE_MODULE = "knowledge_graph"

# Defensive default for a missing `confidence` field on legacy
# entries (the Sprint-2 typed layer). 0.5 is the midpoint so such
# entries neither dominate nor vanish.
_LEGACY_CONFIDENCE_FALLBACK = 0.5
# Defensive default recency (days). 30 days -- a future sprint could
# expose this as a configurable parameter via a Pydantic graph config.
_LEGACY_RECENCY_DAYS_FALLBACK = 30.0


class KnowledgeGraph:
    """The Knowledge Graph runtime. Read-only over `MemoryReader`.

    Construction mirrors every other module: `memory` is required
    (no useful default -- BFS over what?). `event_bus` is optional;
    `agent_id` is the agent whose permission boundary is consulted
    for every Memory read.
    """

    def __init__(
        self,
        *,
        memory: MemoryReader,
        event_bus: EventBus | None = None,
        agent_id: str = "knowledge_graph",
    ) -> None:
        self._memory = memory
        self._bus = event_bus
        self._agent_id = agent_id
        if not isinstance(memory, MemoryReader):
            # The Protocol is duck-typed at runtime; this guard
            # surfaces a configuration error early rather than at
            # the first BFS call, which would just emit empty
            # results.
            raise GraphConfigError(
                "KnowledgeGraph requires a MemoryReader; pass a MemoryManager or a test stub "
                "that satisfies MemoryReader."
            )

    # ====================================================================== #
    # neighbourhood -- BFS from a seed over typed edges
    # ====================================================================== #
    async def neighbourhood(
        self,
        *,
        requesting_agent_id: str,
        seed_id: uuid.UUID,
        max_hops: int = 2,
        min_confidence: float = 0.0,
        relationship_types: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Neighbour]:
        """BFS over typed outbound relationships from `seed_id`,
        up to `max_hops`. Returns one `Neighbour` per reachable entry,
        ranked by `path_score` descending.

        `min_confidence` filters out the target of an edge if the
        target's `confidence` is below the floor (legacy entries
        with `confidence=None` are treated as `0.5`).

        `relationship_types` filters the traversal to the listed
        types only. When `None`, all types are eligible.
        """
        if max_hops < 1:
            raise GraphConfigError("max_hops must be >= 1")
        requester = requesting_agent_id or self._agent_id

        seed = await self._memory.get(requesting_agent_id=requester, entry_id=seed_id)
        if seed is None:
            # A seed the requester can't see OR doesn't exist --
            # both surface as an empty neighbourhood. Never raise;
            # unreadable/unknown seeds are part of the data model,
            # not an error condition.
            await self._publish_traversal(requester, "neighbourhood", seed_ids=[seed_id], result_count=0)
            return []

        type_filter = set(relationship_types) if relationship_types else None

        # BFS state. `visited` includes the seed so we don't revisit;
        # the seed itself is not a neighbour of itself.
        visited: dict[uuid.UUID, tuple[int, float, list[str]]] = {
            seed_id: (0, 1.0, []),
        }
        frontier: deque[tuple[uuid.UUID, int, float, list[str]]] = deque([(seed_id, 0, 1.0, [])])
        # Each expansion enqueues the *next* neighbour into visited
        # so subsequent reachable paths compute their score
        # against an already-visited node without producing a
        # duplicate. The captured tuple is the shortest path so far
        # (BFS naturally yields shortest-hop paths; ties at equal
        # hops preserve the first discovered = highest path_score).
        neighbours: list[Neighbour] = []

        while frontier:
            current, hops, accumulated_score, accumulated_types = frontier.popleft()
            if hops >= max_hops:
                continue
            try:
                edges = await self._memory.find_relationships(
                    requesting_agent_id=requester,
                    entry_id=current,
                    direction="outbound",
                )
            except AttributeError:
                edges = []
            for edge in edges:
                if type_filter is not None and edge.relationship_type not in type_filter:
                    continue
                target = await self._memory.get(requesting_agent_id=requester, entry_id=edge.target_entry_id)
                if target is None:
                    continue
                if min_confidence > 0.0:
                    confidence = target.confidence if target.confidence is not None else _LEGACY_CONFIDENCE_FALLBACK
                    if confidence < min_confidence:
                        continue
                new_score = max(0.0, min(1.0, accumulated_score * edge.weight))
                new_types = accumulated_types + [edge.relationship_type]
                new_hops = hops + 1
                if edge.target_entry_id in visited:
                    # Keep the higher path_score for ties at the
                    # same hop count; the BFS frontier already
                    # encountered this node at this hop count.
                    prev_hops, prev_score, _ = visited[edge.target_entry_id]
                    if prev_hops == new_hops and new_score > prev_score:
                        visited[edge.target_entry_id] = (new_hops, new_score, new_types)
                        # Update the previously-emitted Neighbour's
                        # path_score and path_edge_types in-place --
                        # by id, since Neighbour holds the entry
                        # itself.
                        for nb in neighbours:
                            if nb.entry.id == edge.target_entry_id:
                                nb.path_score = new_score
                                nb.path_edge_types = new_types
                                break
                    continue
                visited[edge.target_entry_id] = (new_hops, new_score, new_types)
                neighbours.append(
                    Neighbour(
                        entry=target,
                        distance=new_hops,
                        path_score=new_score,
                        path_edge_types=list(new_types),
                    )
                )
                if new_hops < max_hops:
                    frontier.append((edge.target_entry_id, new_hops, new_score, new_types))

        neighbours.sort(key=lambda n: (-n.path_score, n.distance, str(n.entry.id)))
        if limit is not None and limit >= 0:
            neighbours = neighbours[:limit]

        await self._publish_traversal(
            requester, "neighbourhood", seed_ids=[seed_id], result_count=len(neighbours)
        )
        return neighbours

    # ====================================================================== #
    # expansion -- structural + tag-overlap fan-out
    # ====================================================================== #
    async def expansion(
        self,
        *,
        requesting_agent_id: str,
        seed_ids: list[uuid.UUID],
        max_hops: int = 1,
        limit: int | None = None,
    ) -> ExpandedContext:
        """One-hop structural fan-out from a *set* of seeds.

        The expansion score is:

            expansion_score(node) = (
                typed_edge_score   # 1.0 per direct typed edge from any seed
                + tag_overlap      # 0.5 * (|shared_tags| / |node_tags|) if any overlap
                + backlink_score   # 0.3 per backlink from any seed
            )

        clamped to [0.0, 1.0]. Today's "semantic" is structural + tag-
        overlap; `Knowledge Graph.md`'s Future Considerations call out
        a future vector retrieval -- which would integrate as a third
        term, not a replacement.

        Seeds are excluded from the result set.
        """
        if max_hops < 1:
            raise GraphConfigError("max_hops must be >= 1")
        requester = requesting_agent_id or self._agent_id

        resolved_seeds: list[tuple[uuid.UUID, MemoryEntry]] = []
        for sid in seed_ids:
            entry = await self._memory.get(requesting_agent_id=requester, entry_id=sid)
            if entry is not None:
                resolved_seeds.append((sid, entry))
        if not resolved_seeds:
            await self._publish_expansion(requester, seed_ids, 0)
            return ExpandedContext(seeds=[s for s, _ in resolved_seeds], nodes=[], max_hops=max_hops)

        # Build the union of tags across seeds for the overlap term.
        seed_tag_union: set[str] = set()
        for _, entry in resolved_seeds:
            seed_tag_union.update(entry.tags)
        seed_ids_set = {sid for sid, _ in resolved_seeds}
        score_by_id: dict[uuid.UUID, float] = defaultdict(float)
        edge_type_by_id: dict[uuid.UUID, list[str]] = defaultdict(list)
        for target_id, score_inc, rel_type in await self._typed_edge_increments(
            requester=requester, seeds=resolved_seeds, max_hops=max_hops
        ):
            if target_id in seed_ids_set:
                continue
            score_by_id[target_id] += score_inc
            if rel_type is not None and rel_type not in edge_type_by_id[target_id]:
                edge_type_by_id[target_id].append(rel_type)

        # Tag-overlap term. This runs over BOTH nodes already scored
        # via typed edges AND nodes that are tag-overlapping only.
        # Nodes that have no typed edge but DO have overlapping tags
        # are added here so the structural+tag hybrid is truly hybrid
        # (not "structural only when typed edge exists").
        scored_ids = list(score_by_id.keys())
        for target_id in scored_ids:
            target = await self._memory.get(requesting_agent_id=requester, entry_id=target_id)
            if target is None:
                score_by_id.pop(target_id, None)
                continue
            if target.tags and seed_tag_union:
                overlap = len(set(target.tags) & seed_tag_union)
                if overlap:
                    score_by_id[target_id] += 0.5 * (overlap / len(target.tags))

        # Tag-overlap-only: scan all entries in Memory that share
        # tags with the seed union but were NOT surfaced by typed
        # edges. We pull via `query(tags=...)` for each shared tag
        # so a single Memory module call backs the lookup. This is
        # the "pure semantic" expansion branch the heuristic
        # supports today.
        shared_tags = list(seed_tag_union)
        for tag in shared_tags:
            try:
                tagged = await self._memory.query(
                    requesting_agent_id=requester, tags=[tag]
                )
            except AttributeError:
                tagged = []
            for entry in tagged:
                if entry.id in seed_ids_set:
                    continue
                if entry.id in score_by_id:
                    continue
                if not entry.tags:
                    continue
                overlap = len(set(entry.tags) & seed_tag_union)
                if overlap:
                    score_by_id[entry.id] = 0.5 * (overlap / len(entry.tags))

        # Backlink term (looser; reverse-edge of an untyped link).
        for seed_id, _ in resolved_seeds:
            try:
                backlink_entries = await self._memory.get_backlinks(
                    requesting_agent_id=requester, entry_id=seed_id
                )
            except AttributeError:
                backlink_entries = []
            for be in backlink_entries:
                if be.id in seed_ids_set or await self._memory.get(
                    requesting_agent_id=requester, entry_id=be.id
                ) is None:
                    continue
                score_by_id[be.id] += 0.3

        # Materialize results.
        neighbours: list[Neighbour] = []
        for target_id, raw_score in score_by_id.items():
            target = await self._memory.get(requesting_agent_id=requester, entry_id=target_id)
            if target is None:
                continue
            neighbours.append(
                Neighbour(
                    entry=target,
                    distance=1,
                    path_score=max(0.0, min(1.0, raw_score)),
                    path_edge_types=edge_type_by_id.get(target_id, []),
                )
            )
        neighbours.sort(key=lambda n: (-n.path_score, str(n.entry.id)))
        if limit is not None and limit >= 0:
            neighbours = neighbours[:limit]

        await self._publish_expansion(requester, seed_ids, len(neighbours))
        return ExpandedContext(
            seeds=seed_ids,
            nodes=neighbours,
            strategy="hybrid",
            max_hops=max_hops,
        )

    async def _typed_edge_increments(
        self,
        *,
        requester: str,
        seeds: list[tuple[uuid.UUID, MemoryEntry]],
        max_hops: int,
    ) -> list[tuple[uuid.UUID, float, str | None]]:
        """Walk the typed edges outward from each seed by `max_hops`,
        yielding (target_id, score_increment, relationship_type) tuples.

        The increment per typed edge is 1.0 -- one direct typed edge
        is the strongest structural signal, so it dominates tag-overlap
        and backlink terms.
        """
        increments: list[tuple[uuid.UUID, float, str | None]] = []
        visited: set[uuid.UUID] = set()
        for seed_id, _ in seeds:
            frontier: deque[tuple[uuid.UUID, int]] = deque([(seed_id, 0)])
            while frontier:
                current, hops = frontier.popleft()
                if hops >= max_hops:
                    continue
                try:
                    edges = await self._memory.find_relationships(
                        requesting_agent_id=requester, entry_id=current, direction="outbound"
                    )
                except AttributeError:
                    edges = []
                for edge in edges:
                    target_id = edge.target_entry_id
                    if target_id in visited:
                        continue
                    visited.add(target_id)
                    increments.append((target_id, 1.0, edge.relationship_type))
                    frontier.append((target_id, hops + 1))
        return increments

    # ====================================================================== #
    # influence_score -- inbound-edge aggregation
    # ====================================================================== #
    async def influence_score(
        self,
        *,
        requesting_agent_id: str,
        entry_id: uuid.UUID,
        candidate_set_ids: list[uuid.UUID],
    ) -> InfluenceBreakdown:
        """Compute the influence score for `entry_id` against
        `candidate_set_ids`.

            contribution(edge) = edge.weight * source.confidence / (1 + age_in_days)
            influence_total    = clamp(sum(contributions), 0.0, 1.0)

        Edges from entries *outside* the candidate set are ignored
        -- influence is a relative measure (an entry's influence
        *within* a candidate set), not a global one. This matches
        the spec's "Knowledge Graph ... allows the system to
        understand which entries matter most *within the current
        context*" framing.

        Legacy entries (confidence=None) are treated as
        `_LEGACY_CONFIDENCE_FALLBACK`. `created_at` defaults to
        `utcnow - 30 days` when missing.
        """
        requester = requesting_agent_id or self._agent_id

        target = await self._memory.get(requesting_agent_id=requester, entry_id=entry_id)
        if target is None:
            return InfluenceBreakdown(
                entry_id=entry_id,
                score=0.0,
                weighted_contributions=[],
                inbound_edge_count=0,
            )

        candidate_set = set(candidate_set_ids)
        now = datetime.now(timezone.utc)
        contributions: list[float] = []
        inbound_edge_count = 0

        for source_id in candidate_set_ids:
            try:
                inbound_edges = await self._memory.find_relationships(
                    requesting_agent_id=requester,
                    entry_id=source_id,
                    direction="outbound",
                )
            except AttributeError:
                inbound_edges = []
            for edge in inbound_edges:
                if edge.target_entry_id != entry_id:
                    continue
                inbound_edge_count += 1
                source_entry = await self._memory.get(
                    requesting_agent_id=requester, entry_id=source_id
                )
                if source_entry is None:
                    continue
                confidence = (
                    source_entry.confidence
                    if source_entry.confidence is not None
                    else _LEGACY_CONFIDENCE_FALLBACK
                )
                age_days = _age_days(source_entry.created_at, now)
                contribution = (edge.weight * confidence) / (1.0 + age_days)
                contributions.append(contribution)

        score = clamp(sum(contributions), 0.0, 1.0)
        await self._publish_influence(requester, entry_id, score, inbound_edge_count)
        return InfluenceBreakdown(
            entry_id=entry_id,
            score=score,
            weighted_contributions=contributions,
            inbound_edge_count=inbound_edge_count,
        )

    async def _backlinks_safe(self, requester: str, entry_id: uuid.UUID) -> list[MemoryEntry]:
        try:
            return await self._memory.get_backlinks(requesting_agent_id=requester, entry_id=entry_id)
        except AttributeError:
            return []

    # ====================================================================== #
    # propagated_confidence -- shortest-path product
    # ====================================================================== #
    async def propagated_confidence(
        self,
        *,
        requesting_agent_id: str,
        from_id: uuid.UUID,
        to_id: uuid.UUID,
        max_hops: int = 4,
    ) -> PropagatedConfidence:
        """Find the shortest typed path from `from_id` to `to_id`
        (reusing `KnowledgeGraph`'s own BFS; we don't go through
        `MemoryManager.find_path` because we also need edge weights
        and the path length to be reused elsewhere).
        """
        if from_id == to_id:
            # A self-loop path has value == source confidence, by
            # convention (zero length, no edges).
            source = await self._memory.get(requesting_agent_id=requesting_agent_id or self._agent_id, entry_id=from_id)
            value = source.confidence if source and source.confidence is not None else _LEGACY_CONFIDENCE_FALLBACK
            return PropagatedConfidence(
                from_id=from_id,
                to_id=to_id,
                value=clamp(value, 0.0, 1.0),
                path=[from_id],
                hops=0,
                found=True,
            )
        if max_hops < 1:
            raise GraphConfigError("max_hops must be >= 1")
        requester = requesting_agent_id or self._agent_id

        source = await self._memory.get(requesting_agent_id=requester, entry_id=from_id)
        if source is None:
            return PropagatedConfidence(from_id=from_id, to_id=to_id)
        target = await self._memory.get(requesting_agent_id=requester, entry_id=to_id)
        if target is None:
            return PropagatedConfidence(from_id=from_id, to_id=to_id)

        source_confidence = (
            source.confidence if source.confidence is not None else _LEGACY_CONFIDENCE_FALLBACK
        )

        # BFS for the *highest-weight* path within `max_hops`. We
        # track the best score per node, not the first discovered,
        # because edge weights matter (unlike plain shortest-hop
        # BFS).
        best: dict[uuid.UUID, tuple[float, list[uuid.UUID], list[str]]] = {
            from_id: (1.0, [from_id], []),
        }
        frontier: deque[tuple[uuid.UUID, list[uuid.UUID], list[str], float]] = deque(
            [(from_id, [from_id], [], 1.0)]
        )

        while frontier:
            current, path, types, score = frontier.popleft()
            if len(path) - 1 >= max_hops:
                continue
            try:
                edges = await self._memory.find_relationships(
                    requesting_agent_id=requester, entry_id=current, direction="outbound"
                )
            except AttributeError:
                edges = []
            for edge in edges:
                new_score = clamp(score * edge.weight, 0.0, 1.0)
                if edge.target_entry_id not in best or new_score > best[edge.target_entry_id][0]:
                    new_path = path + [edge.target_entry_id]
                    new_types = types + [edge.relationship_type]
                    best[edge.target_entry_id] = (new_score, new_path, new_types)
                    if edge.target_entry_id == to_id:
                        # Found the best path to target; BFS is
                        # ordered by discovery, but the priority
                        # queue below ensures the highest-score path
                        # is preferred over a longer lower-score one.
                        pass
                    frontier.append((edge.target_entry_id, new_path, new_types, new_score))

        if to_id not in best:
            return PropagatedConfidence(from_id=from_id, to_id=to_id)

        final_score, final_path, final_types = best[to_id]
        value = clamp(source_confidence * final_score, 0.0, 1.0)
        return PropagatedConfidence(
            from_id=from_id,
            to_id=to_id,
            value=value,
            path=final_path,
            hops=len(final_types),
            found=True,
        )

    # ====================================================================== #
    # Event publication
    # ====================================================================== #
    async def _publish_traversal(
        self, requester: str, operation: str, *, seed_ids: list[uuid.UUID], result_count: int
    ) -> None:
        await self._publish(
            kg_events.KG_TRAVERSAL_PERFORMED,
            {
                "operation": operation,
                "requester": requester,
                "seed_ids": [str(s) for s in seed_ids],
                "result_count": result_count,
            },
        )

    async def _publish_expansion(
        self, requester: str, seed_ids: list[uuid.UUID], result_count: int
    ) -> None:
        await self._publish(
            kg_events.KG_EXPANSION_PERFORMED,
            {
                "requester": requester,
                "seed_ids": [str(s) for s in seed_ids],
                "result_count": result_count,
            },
        )

    async def _publish_influence(
        self, requester: str, entry_id: uuid.UUID, score: float, inbound_edge_count: int
    ) -> None:
        await self._publish(
            kg_events.KG_INFLUENCE_COMPUTED,
            {
                "requester": requester,
                "entry_id": str(entry_id),
                "score": score,
                "inbound_edge_count": inbound_edge_count,
            },
        )

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(
                Event(
                    event_type=event_type,
                    source_module=SOURCE_MODULE,
                    correlation_id=uuid.uuid4(),
                    payload=payload,
                )
            )
        except Exception:
            # Publishing is best-effort; a bus failure must not fail
            # the read-only graph operation that produced the event.
            return


# --------------------------------------------------------------------------- #
# Module-level helpers
# --------------------------------------------------------------------------- #


def clamp(value: float, lo: float, hi: float) -> float:
    """Numeric clamp. Default 0.0 / 1.0 for `influence_score` and
    `propagated_confidence`."""
    return max(lo, min(hi, value))


def _age_days(created_at: datetime | None, now: datetime) -> float:
    """Days between `created_at` and `now`, with defensive defaults."""
    if created_at is None:
        return _LEGACY_RECENCY_DAYS_FALLBACK
    delta = (now - created_at).total_seconds() / 86400.0
    if delta < 0:
        return 0.0
    return delta
