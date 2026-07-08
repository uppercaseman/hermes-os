"""Context Builder -- assemble the most relevant memories for a request.

The Builder is the assembly layer the Reasoning Engine consumes. It
combines:

1. The explicit seed set (the request's `seed_ids`).
2. The Knowledge Graph's typed-edge traversal (`neighbourhood`).
3. The Knowledge Graph's structural + tag-overlap expansion
   (`expansion`).
4. The Knowledge Graph's confidence propagation (`propagated_confidence`).

into one ordered `AssembledContext` of up to `k` entries, with a
per-entry scoring trace.

The scoring heuristic is:

    final_score(entry) = (
        0.5 * propagated_confidence(seeds -> entry)
        + 0.3 * path_score (from typed-edge traversal)
        + 0.2 * entry.confidence
    )

clamped to [0.0, 1.0]. The weights were chosen so confidence and
typed-edge traversal share the budget equally, with a small
relevance-penalty floor from the entry's own confidence.

The Builder is read-only over Memory and the KG -- it never writes.
The Reflection Engine remains the single writer of cognitive memory.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.context_builder import events as cb_events
from hermes.modules.context_builder.contracts import GraphReader
from hermes.modules.context_builder.errors import (
    ContextBuilderConfigError,
    EmptyContextError,
)
from hermes.modules.context_builder.models import (
    AssembledContext,
    ContextRequest,
    ContextScoreEntry,
)
from hermes.modules.knowledge_graph.models import ExpandedContext, Neighbour
from hermes.modules.knowledge_graph.service import MemoryReader as _KG_MemoryReader
from hermes.modules.memory_manager.models import MemoryEntry

SOURCE_MODULE = "context_builder"

# Scoring weights. Picked so propagated confidence and path score
# are co-equal, plus a small entry-level confidence floor. Clamp
# at the end. These are module-internal constants, not exposed --
# a future ADR that wants to retune them would do so here.
_WEIGHT_PROPAGATED = 0.5
_WEIGHT_PATH = 0.3
_WEIGHT_ENTRY = 0.2


class ContextBuilder:
    """The Context Builder runtime.

    Construction mirrors every other module: `memory` and `kg` are
    required (no useful default -- assembly needs both). `event_bus`
    and `agent_id` are optional.
    """

    def __init__(
        self,
        *,
        memory: _KG_MemoryReader,
        kg: GraphReader,
        event_bus: EventBus | None = None,
        agent_id: str = "context_builder",
    ) -> None:
        self._memory = memory
        self._kg = kg
        self._bus = event_bus
        self._agent_id = agent_id

    # ====================================================================== #
    # assemble
    # ====================================================================== #
    async def assemble(self, request: ContextRequest) -> AssembledContext:
        """Assemble the most relevant memories for `request`.

        Steps:

        1. Fetch the seeds (silently skip unreadable / missing ones).
        2. For each seed, run a `neighbourhood` BFS at
           `request.max_hops`.
        3. Run a single `expansion` over the full seed set.
        4. Score each candidate entry using the weighted sum above.
        5. Cap to `request.k`.
        6. Return the ordered `AssembledContext`.

        Raises `ContextBuilderConfigError` for invalid request
        parameters (e.g. `k <= 0`). Raises `EmptyContextError` if
        no entry survives the filters -- callers can opt to
        swallow this via `try/except`.
        """
        self._validate_request(request)
        requester = request.requesting_agent_id or self._agent_id

        # Step 1 -- resolve seeds.
        resolved_seeds: list[MemoryEntry] = []
        for sid in request.seed_ids:
            entry = await self._memory.get(requesting_agent_id=requester, entry_id=sid)
            if entry is not None:
                resolved_seeds.append(entry)

        if not resolved_seeds:
            await self._publish_fail(requester, request, "no_seeds_resolvable")
            raise EmptyContextError(
                f"no seeds could be resolved for request {request.seed_ids!r}"
            )

        # Step 2 -- per-seed neighbourhood.
        neighbours_by_id: dict[uuid.UUID, Neighbour] = {}
        for seed in resolved_seeds:
            try:
                neighbours = await self._kg.neighbourhood(
                    requesting_agent_id=requester,
                    seed_id=seed.id,
                    max_hops=request.max_hops,
                    min_confidence=request.min_confidence,
                )
            except AttributeError:
                neighbours = []
            for n in neighbours:
                # Take the best (lowest-distance; highest-path-score)
                # per candidate id.
                if n.entry.id not in neighbours_by_id:
                    neighbours_by_id[n.entry.id] = n
                else:
                    prev = neighbours_by_id[n.entry.id]
                    if (n.distance, -n.path_score) < (prev.distance, -prev.path_score):
                        neighbours_by_id[n.entry.id] = n

        # Step 3 -- expansion across all seeds.
        expansion: ExpandedContext | None = None
        try:
            expansion = await self._kg.expansion(
                requesting_agent_id=requester,
                seed_ids=[s.id for s in resolved_seeds],
                max_hops=1,
            )
        except AttributeError:
            expansion = None

        # Merge candidates. Seeds are scored below explicitly as
        # "direct_seed" entries; everything else gets a neighbour
        # or expansion score. Apply `min_confidence` here because
        # expansion (unlike neighbourhood) doesn't take a
        # min_confidence filter at the KG level.
        candidates: dict[uuid.UUID, Neighbour] = {}
        for nb in neighbours_by_id.values():
            candidates[nb.entry.id] = nb
        if expansion is not None:
            for nb in expansion.nodes:
                if nb.entry.id not in candidates:
                    # Honour min_confidence at the assembly boundary.
                    confidence = (
                        nb.entry.confidence
                        if nb.entry.confidence is not None
                        else 0.5
                    )
                    if confidence < request.min_confidence:
                        continue
                    candidates[nb.entry.id] = nb

        # Step 4 -- per-entry scoring.
        scored: list[tuple[MemoryEntry, ContextScoreEntry]] = []
        # Seeds first -- but only if they pass the min_confidence floor
        # (otherwise the request's quality gate filters them out too,
        # and the seed set contributed no useful content).
        for seed in resolved_seeds:
            seed_conf = seed.confidence if seed.confidence is not None else 0.5
            if seed_conf < request.min_confidence:
                continue
            score, propagated = await self._score_seed(
                requester=requester,
                seed=seed,
                request=request,
                resolved_seeds=resolved_seeds,
            )
            scored.append(
                (
                    seed,
                    ContextScoreEntry(
                        entry_id=seed.id,
                        score=score,
                        method="direct_seed",
                        distance=0,
                        propagated_confidence=propagated,
                        path_score=1.0,
                    ),
                )
            )

        # Candidates (seeds excluded -- already in scored).
        seed_id_set = {s.id for s in resolved_seeds}
        for cid, nb in candidates.items():
            if cid in seed_id_set:
                continue
            propagated_total, propagated_values = await self._propagated_across_seeds(
                requester=requester,
                target_id=cid,
                seeds=resolved_seeds,
                max_hops=request.max_hops,
            )
            path_score = nb.path_score
            entry = nb.entry
            entry_confidence = (
                entry.confidence if entry.confidence is not None else 0.5
            )
            raw = (
                _WEIGHT_PROPAGATED * propagated_total
                + _WEIGHT_PATH * path_score
                + _WEIGHT_ENTRY * entry_confidence
            )
            score = clamp(raw, 0.0, 1.0)
            method = "neighbour" if nb in neighbours_by_id.values() else "expansion"
            scored.append(
                (
                    entry,
                    ContextScoreEntry(
                        entry_id=cid,
                        score=score,
                        method=method,
                        distance=nb.distance,
                        propagated_confidence=propagated_total,
                        path_score=path_score,
                    ),
                )
            )

        # Step 5 -- cap to k.
        scored.sort(key=lambda pair: (-pair[1].score, str(pair[0].id)))
        scored = scored[: max(0, request.k)]

        if not scored:
            await self._publish_fail(requester, request, "no_candidates_after_filter")
            raise EmptyContextError(
                "context assembly produced zero entries after filtering"
            )

        entries = [pair[0] for pair in scored]
        trace = [pair[1] for pair in scored]

        context = AssembledContext(
            request=request,
            entries=entries,
            scoring_trace=trace,
            assembled_at=datetime.now(timezone.utc),
            metadata={
                "requester": requester,
                "mission_id": str(request.mission_id) if request.mission_id else "",
                "seed_count": str(len(resolved_seeds)),
                "candidate_count": str(len(candidates)),
            },
        )

        await self._publish_built(requester, request, context)
        return context

    # ====================================================================== #
    # Per-seed scoring helpers
    # ====================================================================== #
    async def _score_seed(
        self,
        *,
        requester: str,
        seed: MemoryEntry,
        request: ContextRequest,
        resolved_seeds: list[MemoryEntry],
    ) -> tuple[float, float]:
        """Score a seed entry as "the most relevant already-known
        thing." A seed's own confidence is the dominant signal; the
        propagated confidence is the average across the other
        seeds."""
        seed_conf = seed.confidence if seed.confidence is not None else 0.5
        other_seeds = [s for s in resolved_seeds if s.id != seed.id]
        propagated_avg = 0.0
        if other_seeds:
            total = 0.0
            count = 0
            for src in other_seeds:
                prop = await self._safe_propagated(
                    requester=requester,
                    from_id=src.id,
                    to_id=seed.id,
                    max_hops=request.max_hops,
                )
                total += prop
                count += 1
            propagated_avg = total / count if count else 0.0
        raw = (
            _WEIGHT_PROPAGATED * propagated_avg
            + _WEIGHT_PATH * 1.0  # a seed is, by definition, fully relevant
            + _WEIGHT_ENTRY * seed_conf
        )
        return clamp(raw, 0.0, 1.0), clamp(propagated_avg, 0.0, 1.0)

    async def _propagated_across_seeds(
        self,
        *,
        requester: str,
        target_id: uuid.UUID,
        seeds: list[MemoryEntry],
        max_hops: int,
    ) -> tuple[float, list[float]]:
        """Average propagated confidence from each seed to a target."""
        if not seeds:
            return 0.0, []
        values: list[float] = []
        for seed in seeds:
            values.append(
                await self._safe_propagated(
                    requester=requester,
                    from_id=seed.id,
                    to_id=target_id,
                    max_hops=max_hops,
                )
            )
        return (sum(values) / len(values)) if values else 0.0, values

    async def _safe_propagated(
        self,
        *,
        requester: str,
        from_id: uuid.UUID,
        to_id: uuid.UUID,
        max_hops: int,
    ) -> float:
        try:
            result = await self._kg.propagated_confidence(
                requesting_agent_id=requester,
                from_id=from_id,
                to_id=to_id,
                max_hops=max_hops,
            )
            return result.value if result.found else 0.0
        except (AttributeError, Exception):
            return 0.0

    # ====================================================================== #
    # Validation + event publication
    # ====================================================================== #
    def _validate_request(self, request: ContextRequest) -> None:
        if request.k <= 0:
            raise ContextBuilderConfigError(f"k must be >= 1; got {request.k}")
        if not request.seed_ids:
            raise ContextBuilderConfigError("seed_ids must be non-empty")
        if not 0.0 <= request.min_confidence <= 1.0:
            raise ContextBuilderConfigError(
                f"min_confidence must be in [0.0, 1.0]; got {request.min_confidence}"
            )

    async def _publish_built(
        self, requester: str, request: ContextRequest, context: AssembledContext
    ) -> None:
        await self._publish(
            cb_events.CONTEXT_BUILT,
            {
                "requester": requester,
                "mission_id": str(request.mission_id) if request.mission_id else "",
                "seed_count": str(len(request.seed_ids)),
                "entry_count": str(len(context.entries)),
            },
        )

    async def _publish_fail(
        self, requester: str, request: ContextRequest, reason: str
    ) -> None:
        await self._publish(
            cb_events.CONTEXT_BUILD_FAILED,
            {
                "requester": requester,
                "reason": reason,
                "seed_count": str(len(request.seed_ids)),
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
            return


# --------------------------------------------------------------------------- #
# Module-level helpers
# --------------------------------------------------------------------------- #


def clamp(value: float, lo: float, hi: float) -> float:
    """Numeric clamp."""
    return max(lo, min(hi, value))
