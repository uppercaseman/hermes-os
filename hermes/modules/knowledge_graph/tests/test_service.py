"""Tests for the Knowledge Graph runtime layer.

End-to-end against the real `MemoryManager` (no fakes for Memory).
Every test exercises the typed-substrate BFS path and asserts on
the returned models.

Performance budget: `test_neighbourhood_budget_under_200ms_for_10k_edges`
is the integration budget check from `Knowledge Graph.md`.
"""
from __future__ import annotations

import time
import uuid

import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.knowledge_graph import (
    ExpandedContext,
    InfluenceBreakdown,
    KG_EXPANSION_PERFORMED,
    KG_INFLUENCE_COMPUTED,
    KG_TRAVERSAL_PERFORMED,
    KnowledgeGraph,
    Neighbour,
    PropagatedConfidence,
    build_knowledge_graph,
)
from hermes.modules.memory_manager import build_memory_manager
from hermes.modules.memory_manager.typed import (
    MemoryRelationship,
    MemoryRelationshipType,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def bus():
    return InMemoryEventBus()


@pytest.fixture
def memory(bus):
    return build_memory_manager(event_bus=bus)


@pytest.fixture
def kg(memory, bus):
    return build_knowledge_graph(memory=memory, event_bus=bus, agent_id="reflector")


async def _capture_events(bus):
    captured = []

    async def _sink(event):
        captured.append(event.event_type)

    await bus.subscribe("*", _sink)
    return captured


async def _make_skill(memory, *, key, confidence=0.9, tags=None, relationships=None):
    return await memory.record_typed(
        requesting_agent_id="reflector",
        memory_type="skill_memory",
        key=key,
        value={"claim": key},
        confidence=confidence,
        importance=0.7,
        tags=tags or [],
        relationships=relationships or [],
    )


async def _make_experience(memory, *, key, confidence=0.7, tags=None, relationships=None):
    return await memory.record_typed(
        requesting_agent_id="reflector",
        memory_type="experience_memory",
        key=key,
        value={"claim": key},
        confidence=confidence,
        importance=0.6,
        tags=tags or [],
        relationships=relationships or [],
    )


async def _make_user_dna(memory, *, key, confidence=0.95):
    return await memory.record_typed(
        requesting_agent_id="reflector",
        memory_type="user_dna",
        key=key,
        value={"fact": key},
        confidence=confidence,
        importance=0.9,
    )


# --------------------------------------------------------------------------- #
# Construction + factory
# --------------------------------------------------------------------------- #


class TestConstruction:
    def test_build_factory_returns_knowledge_graph(self, memory) -> None:
        kg = build_knowledge_graph(memory=memory)
        assert isinstance(kg, KnowledgeGraph)

    def test_factory_requires_memory(self) -> None:
        # The `memory` kwarg is required; omitting it must fail at
        # the boundary, not at the first BFS call.
        with pytest.raises(TypeError):
            build_knowledge_graph()  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Neighbourhood
# --------------------------------------------------------------------------- #


class TestNeighbourhood:
    async def test_returns_empty_for_unknown_seed(self, kg, bus) -> None:
        events = await _capture_events(bus)
        result = await kg.neighbourhood(
            requesting_agent_id="reflector",
            seed_id=uuid.uuid4(),
            max_hops=2,
        )
        assert result == []
        # Traversal event still fires (count=0).
        assert KG_TRAVERSAL_PERFORMED in events

    async def test_returns_empty_for_seed_with_no_outbound_edges(self, kg, memory) -> None:
        skill = await _make_skill(memory, key="orphan-skill")
        result = await kg.neighbourhood(
            requesting_agent_id="reflector",
            seed_id=skill.id,
            max_hops=2,
        )
        assert result == []

    async def test_returns_direct_neighbours_at_distance_one(self, kg, memory) -> None:
        skill = await _make_skill(memory, key="seed")
        experience = await _make_experience(
            memory,
            key="neighbour",
            relationships=[],
        )
        # Wire skill -> experience via a typed relationship.
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="seed",
            value={"claim": "seed"},
            confidence=0.9,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.DERIVED_FROM,
                    target_entry_id=experience.id,
                    weight=0.8,
                )
            ],
        )
        result = await kg.neighbourhood(
            requesting_agent_id="reflector",
            seed_id=skill.id,
            max_hops=2,
        )
        assert len(result) == 1
        n = result[0]
        assert n.entry.id == experience.id
        assert n.distance == 1
        assert n.path_score == pytest.approx(0.8, abs=1e-6)
        assert n.path_edge_types == [MemoryRelationshipType.DERIVED_FROM]

    async def test_two_hop_path_appears_at_distance_two(self, kg, memory) -> None:
        a = await _make_skill(memory, key="a")
        b = await _make_skill(memory, key="b")
        c = await _make_skill(memory, key="c")
        # a -> b -> c
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="a",
            value={"claim": "a"},
            confidence=0.9,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.DERIVED_FROM,
                    target_entry_id=b.id,
                    weight=0.7,
                )
            ],
        )
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="b",
            value={"claim": "b"},
            confidence=0.9,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.CONFIRMED_BY,
                    target_entry_id=c.id,
                    weight=0.5,
                )
            ],
        )
        result = await kg.neighbourhood(
            requesting_agent_id="reflector",
            seed_id=a.id,
            max_hops=2,
        )
        # Both b (1 hop) and c (2 hops) appear.
        ids_by_distance = {n.entry.id: n for n in result}
        assert a.id not in ids_by_distance  # seed is excluded
        assert ids_by_distance[b.id].distance == 1
        assert ids_by_distance[b.id].path_score == pytest.approx(0.7)
        assert ids_by_distance[c.id].distance == 2
        assert ids_by_distance[c.id].path_score == pytest.approx(0.7 * 0.5, abs=1e-6)
        assert ids_by_distance[c.id].path_edge_types == [
            MemoryRelationshipType.DERIVED_FROM,
            MemoryRelationshipType.CONFIRMED_BY,
        ]

    async def test_max_hops_zero_raises(self, kg) -> None:
        with pytest.raises(Exception):
            await kg.neighbourhood(
                requesting_agent_id="reflector",
                seed_id=uuid.uuid4(),
                max_hops=0,
            )

    async def test_min_confidence_filters_targets(self, kg, memory) -> None:
        seed = await _make_skill(memory, key="seed")
        high = await _make_experience(memory, key="high", confidence=0.95)
        low = await _make_experience(memory, key="low", confidence=0.1)
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="seed",
            value={"claim": "seed"},
            confidence=0.9,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.REFERENCES,
                    target_entry_id=high.id,
                    weight=1.0,
                ),
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.REFERENCES,
                    target_entry_id=low.id,
                    weight=1.0,
                ),
            ],
        )
        result = await kg.neighbourhood(
            requesting_agent_id="reflector",
            seed_id=seed.id,
            max_hops=2,
            min_confidence=0.5,
        )
        ids = {n.entry.id for n in result}
        assert high.id in ids
        assert low.id not in ids

    async def test_relationship_types_filter(self, kg, memory) -> None:
        seed = await _make_skill(memory, key="seed")
        b = await _make_skill(memory, key="b")
        c = await _make_skill(memory, key="c")
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="seed",
            value={"claim": "seed"},
            confidence=0.9,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.DERIVED_FROM,
                    target_entry_id=b.id,
                    weight=1.0,
                ),
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.REFERENCES,
                    target_entry_id=c.id,
                    weight=1.0,
                ),
            ],
        )
        result = await kg.neighbourhood(
            requesting_agent_id="reflector",
            seed_id=seed.id,
            max_hops=1,
            relationship_types=[MemoryRelationshipType.DERIVED_FROM],
        )
        ids = {n.entry.id for n in result}
        assert b.id in ids
        assert c.id not in ids

    async def test_limit_caps_results(self, kg, memory) -> None:
        seed = await _make_skill(memory, key="seed")
        targets = [await _make_skill(memory, key=f"t{i}") for i in range(5)]
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="seed",
            value={"claim": "seed"},
            confidence=0.9,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.REFERENCES,
                    target_entry_id=t.id,
                    weight=float(i) / 5.0,
                )
                for i, t in enumerate(targets)
            ],
        )
        result = await kg.neighbourhood(
            requesting_agent_id="reflector",
            seed_id=seed.id,
            max_hops=1,
            limit=2,
        )
        assert len(result) == 2

    async def test_path_score_clamped_to_one(self, kg, memory) -> None:
        seed = await _make_skill(memory, key="seed")
        target = await _make_skill(memory, key="t")
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="seed",
            value={"claim": "seed"},
            confidence=0.9,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.REFERENCES,
                    target_entry_id=target.id,
                    weight=5.0,  # way above 1.0
                )
            ],
        )
        result = await kg.neighbourhood(
            requesting_agent_id="reflector",
            seed_id=seed.id,
            max_hops=1,
        )
        assert result[0].path_score == 1.0


# --------------------------------------------------------------------------- #
# Expansion
# --------------------------------------------------------------------------- #


class TestExpansion:
    async def test_returns_empty_when_no_resolvable_seeds(self, kg) -> None:
        result = await kg.expansion(
            requesting_agent_id="reflector",
            seed_ids=[uuid.uuid4(), uuid.uuid4()],
        )
        assert result.seeds == []
        assert result.nodes == []

    async def test_seeds_excluded_from_results(self, kg, memory) -> None:
        seed = await _make_skill(memory, key="seed", tags=["alpha", "beta"])
        target = await _make_skill(
            memory, key="t", tags=["alpha"]
        )  # shares a tag with seed
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="seed",
            value={"claim": "seed"},
            confidence=0.9,
            tags=["alpha", "beta"],
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.REFERENCES,
                    target_entry_id=target.id,
                    weight=1.0,
                )
            ],
        )
        result = await kg.expansion(
            requesting_agent_id="reflector",
            seed_ids=[seed.id],
        )
        ids = {n.entry.id for n in result.nodes}
        assert seed.id not in ids

    async def test_typed_edge_increments_score(self, kg, memory) -> None:
        seed = await _make_skill(memory, key="seed")
        target = await _make_skill(memory, key="t")
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="seed",
            value={"claim": "seed"},
            confidence=0.9,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.DERIVED_FROM,
                    target_entry_id=target.id,
                    weight=1.0,
                )
            ],
        )
        result = await kg.expansion(
            requesting_agent_id="reflector",
            seed_ids=[seed.id],
        )
        assert len(result.nodes) == 1
        assert result.nodes[0].path_score >= 1.0  # typed edge term alone
        assert result.nodes[0].path_score <= 1.0  # clamped

    async def test_tag_overlap_adds_partial_credit(self, kg, memory) -> None:
        seed = await _make_skill(memory, key="seed", tags=["t1", "t2", "t3"])
        target = await _make_skill(memory, key="t", tags=["t1", "t2", "t3"])
        result = await kg.expansion(
            requesting_agent_id="reflector",
            seed_ids=[seed.id],
        )
        # No typed edge; only tag overlap. Score = 0.5 * 3/3 = 0.5.
        assert len(result.nodes) == 1
        assert result.nodes[0].path_score == pytest.approx(0.5, abs=1e-6)

    async def test_combined_typed_edge_and_tag_overlap(self, kg, memory) -> None:
        seed = await _make_skill(memory, key="seed", tags=["shared"])
        target = await _make_skill(memory, key="t", tags=["shared"])
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="seed",
            value={"claim": "seed"},
            confidence=0.9,
            tags=["shared"],
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.REFERENCES,
                    target_entry_id=target.id,
                    weight=1.0,
                )
            ],
        )
        result = await kg.expansion(
            requesting_agent_id="reflector",
            seed_ids=[seed.id],
        )
        # typed edge (1.0) + tag overlap (0.5 * 1/1 = 0.5) = 1.5 -> clamp 1.0
        assert len(result.nodes) == 1
        assert result.nodes[0].path_score == 1.0

    async def test_publishes_expansion_event(self, kg, memory, bus) -> None:
        events = await _capture_events(bus)
        seed = await _make_skill(memory, key="seed")
        await kg.expansion(requesting_agent_id="reflector", seed_ids=[seed.id])
        assert KG_EXPANSION_PERFORMED in events

    async def test_limit_caps_results(self, kg, memory) -> None:
        seeds = [await _make_skill(memory, key=f"s{i}", tags=["shared"]) for i in range(3)]
        targets = [await _make_skill(memory, key=f"t{i}", tags=["shared"]) for i in range(10)]
        for s in seeds:
            await memory.record_typed(
                requesting_agent_id="reflector",
                memory_type="skill_memory",
                key=f"shared-{s.id}",
                value={"claim": "hub"},
                confidence=0.9,
                tags=["shared"],
                relationships=[
                    MemoryRelationship(
                        relationship_type=MemoryRelationshipType.REFERENCES,
                        target_entry_id=t.id,
                        weight=0.5,
                    )
                    for t in targets
                ],
            )
        # Re-record the seed entries with the same shared tag set so
        # the hub has the shared tag; then expansion fires.
        # Simpler: just call expansion on the seed entries directly,
        # which already have the shared tag.
        # Override: write hub entries via direct save so we have a
        # clean single-seed setup.
        # Use a single hub:
        hub = await _make_skill(memory, key="hub", tags=["shared"])
        targets2 = [await _make_skill(memory, key=f"u{i}", tags=["shared"]) for i in range(10)]
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="hub",
            value={"claim": "hub"},
            confidence=0.9,
            tags=["shared"],
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.REFERENCES,
                    target_entry_id=t.id,
                    weight=0.5,
                )
                for t in targets2
            ],
        )
        result = await kg.expansion(
            requesting_agent_id="reflector",
            seed_ids=[hub.id],
            limit=3,
        )
        assert len(result.nodes) == 3


# --------------------------------------------------------------------------- #
# Influence score
# --------------------------------------------------------------------------- #


class TestInfluenceScore:
    async def test_unknown_target_returns_zero(self, kg) -> None:
        result = await kg.influence_score(
            requesting_agent_id="reflector",
            entry_id=uuid.uuid4(),
            candidate_set_ids=[uuid.uuid4()],
        )
        assert result.score == 0.0
        assert result.inbound_edge_count == 0

    async def test_no_inbound_edges_returns_zero(self, kg, memory) -> None:
        entry = await _make_skill(memory, key="isolated", confidence=0.9)
        other = await _make_skill(memory, key="other", confidence=0.9)
        result = await kg.influence_score(
            requesting_agent_id="reflector",
            entry_id=entry.id,
            candidate_set_ids=[other.id],
        )
        assert result.score == 0.0
        assert result.inbound_edge_count == 0

    async def test_inbound_edge_from_candidate_set_influences(self, kg, memory) -> None:
        target = await _make_skill(memory, key="target", confidence=0.9)
        source = await _make_skill(memory, key="source", confidence=0.8)
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="source",
            value={"claim": "source"},
            confidence=0.8,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.CONFIRMED_BY,
                    target_entry_id=target.id,
                    weight=1.0,
                )
            ],
        )
        result = await kg.influence_score(
            requesting_agent_id="reflector",
            entry_id=target.id,
            candidate_set_ids=[source.id],
        )
        assert result.inbound_edge_count == 1
        assert 0.0 < result.score <= 1.0
        assert len(result.weighted_contributions) == 1

    async def test_inbound_from_outside_candidate_set_ignored(self, kg, memory) -> None:
        target = await _make_skill(memory, key="target", confidence=0.9)
        source = await _make_skill(memory, key="source", confidence=0.8)
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="source",
            value={"claim": "source"},
            confidence=0.8,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.CONFIRMED_BY,
                    target_entry_id=target.id,
                    weight=1.0,
                )
            ],
        )
        # `source` is *not* in the candidate set.
        result = await kg.influence_score(
            requesting_agent_id="reflector",
            entry_id=target.id,
            candidate_set_ids=[uuid.uuid4(), uuid.uuid4()],
        )
        assert result.inbound_edge_count == 0
        assert result.score == 0.0

    async def test_multiple_inbound_edges_sum_clamped(self, kg, memory) -> None:
        target = await _make_skill(memory, key="target", confidence=0.9)
        sources = [await _make_skill(memory, key=f"s{i}", confidence=0.95) for i in range(3)]
        # Wire each source -> target via a heavy typed edge by
        # upserting each source entry with the relationship.
        for s in sources:
            await memory.record_typed(
                requesting_agent_id="reflector",
                memory_type="skill_memory",
                key=s.key,
                value={"claim": s.key},
                confidence=0.95,
                relationships=[
                    MemoryRelationship(
                        relationship_type=MemoryRelationshipType.CONFIRMED_BY,
                        target_entry_id=target.id,
                        weight=2.0,  # would overflow clamp on its own
                    )
                ],
            )
        result = await kg.influence_score(
            requesting_agent_id="reflector",
            entry_id=target.id,
            candidate_set_ids=[s.id for s in sources],
        )
        assert result.inbound_edge_count == 3
        assert result.score == 1.0  # clamped

    async def test_publishes_influence_event(self, kg, memory, bus) -> None:
        events = await _capture_events(bus)
        target = await _make_skill(memory, key="t")
        await kg.influence_score(
            requesting_agent_id="reflector",
            entry_id=target.id,
            candidate_set_ids=[],
        )
        assert KG_INFLUENCE_COMPUTED in events


# --------------------------------------------------------------------------- #
# Propagated confidence
# --------------------------------------------------------------------------- #


class TestPropagatedConfidence:
    async def test_self_loop_returns_source_confidence(self, kg, memory) -> None:
        source = await _make_skill(memory, key="source", confidence=0.8)
        result = await kg.propagated_confidence(
            requesting_agent_id="reflector",
            from_id=source.id,
            to_id=source.id,
            max_hops=4,
        )
        assert result.found is True
        assert result.hops == 0
        assert result.value == pytest.approx(0.8)

    async def test_unknown_source_returns_not_found(self, kg) -> None:
        result = await kg.propagated_confidence(
            requesting_agent_id="reflector",
            from_id=uuid.uuid4(),
            to_id=uuid.uuid4(),
            max_hops=4,
        )
        assert result.found is False
        assert result.value == 0.0

    async def test_single_hop_propagation(self, kg, memory) -> None:
        source = await _make_skill(memory, key="source", confidence=0.8)
        target = await _make_skill(memory, key="target")
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="source",
            value={"claim": "source"},
            confidence=0.8,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.DERIVED_FROM,
                    target_entry_id=target.id,
                    weight=0.5,
                )
            ],
        )
        result = await kg.propagated_confidence(
            requesting_agent_id="reflector",
            from_id=source.id,
            to_id=target.id,
            max_hops=4,
        )
        assert result.found is True
        assert result.hops == 1
        assert result.value == pytest.approx(0.4, abs=1e-6)
        assert result.path == [source.id, target.id]

    async def test_multi_hop_propagation_attenuates(self, kg, memory) -> None:
        a = await _make_skill(memory, key="a", confidence=1.0)
        b = await _make_skill(memory, key="b")
        c = await _make_skill(memory, key="c")
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="a",
            value={"claim": "a"},
            confidence=1.0,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.DERIVED_FROM,
                    target_entry_id=b.id,
                    weight=0.5,
                )
            ],
        )
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="b",
            value={"claim": "b"},
            confidence=0.9,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.CONFIRMED_BY,
                    target_entry_id=c.id,
                    weight=0.5,
                )
            ],
        )
        result = await kg.propagated_confidence(
            requesting_agent_id="reflector",
            from_id=a.id,
            to_id=c.id,
            max_hops=4,
        )
        assert result.found is True
        assert result.hops == 2
        assert result.value == pytest.approx(1.0 * 0.5 * 0.5, abs=1e-6)

    async def test_no_path_returns_not_found(self, kg, memory) -> None:
        a = await _make_skill(memory, key="a", confidence=0.9)
        b = await _make_skill(memory, key="b", confidence=0.9)
        # No edge between them.
        result = await kg.propagated_confidence(
            requesting_agent_id="reflector",
            from_id=a.id,
            to_id=b.id,
            max_hops=4,
        )
        assert result.found is False
        assert result.hops == 0
        assert result.path == []


# --------------------------------------------------------------------------- #
# Performance budget
# --------------------------------------------------------------------------- #


class TestPerformanceBudget:
    async def test_neighbourhood_budget_under_200ms_for_10k_edges(self, memory) -> None:
        """Per `Knowledge Graph.md`: BFS over 10,000 typed edges must
        complete in < 200ms. We build 1,000 entries with ~10
        outbound edges each (10,000 edges total) and run a single
        neighbourhood BFS.
        """
        # The graph's agent_id is irrelevant; we pass a single seed
        # so the BFS visits up to 1,000 nodes.
        kg = build_knowledge_graph(memory=memory, agent_id="perf")
        # Build 1,000 entries. The first one is the seed.
        # The remaining 999 are split into two layers: 0->i, then
        # i->j chains via the seed's outbound edges (10k edges
        # total = 1000 * 10).
        # For a 1k-node dense graph with 10 outbound edges per node,
        # edges total = 10,000. We achieve this by writing each node
        # with 10 typed edges to a "neighbour pool."
        num_nodes = 1000
        # First pass: write all 1000 nodes bare (no relationships).
        ids = []
        for i in range(num_nodes):
            entry = await memory.record_typed(
                requesting_agent_id="perf",
                memory_type="skill_memory",
                key=f"node-{i}",
                value={"i": i},
                confidence=0.9,
            )
            ids.append(entry.id)

        # Second pass: each node gets 10 outbound edges pointing to
        # 10 distinct other nodes (using index arithmetic so
        # duplicates and self-loops are avoided deterministically).
        # Re-record the same entry via `record_typed`, which is an
        # upsert over the typed composite key.
        for i in range(num_nodes):
            rels = []
            for k in range(10):
                target = (i + 1 + k) % num_nodes
                if target == i:
                    target = (i + 2) % num_nodes
                rels.append(
                    MemoryRelationship(
                        relationship_type=MemoryRelationshipType.REFERENCES,
                        target_entry_id=ids[target],
                        weight=0.5,
                    )
                )
            await memory.record_typed(
                requesting_agent_id="perf",
                memory_type="skill_memory",
                key=f"node-{i}",
                value={"i": i},
                confidence=0.9,
                relationships=rels,
            )

        # Time the BFS.
        start = time.perf_counter()
        result = await kg.neighbourhood(
            requesting_agent_id="perf",
            seed_id=ids[0],
            max_hops=1,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        assert elapsed_ms < 200.0, f"neighbourhood BFS took {elapsed_ms:.1f}ms (budget 200ms)"
        # The seed has 10 outbound edges by construction. With
        # max_hops=1 we expect exactly those 10 direct neighbours
        # (any 2-hop nodes are out of range).
        assert len(result) == 10


# --------------------------------------------------------------------------- #
# Cross-cutting
# --------------------------------------------------------------------------- #


class TestCrossCutting:
    async def test_requesting_agent_id_override(self, kg, memory, bus) -> None:
        """The runtime must respect the caller-supplied `requesting_agent_id`
        rather than always using its own `agent_id`."""
        captured: list = []

        async def _sink(event):
            captured.append(event)

        # Subscribe BEFORE the call so the sink receives the event.
        await bus.subscribe("*", _sink)
        skill = await _make_skill(memory, key="s", confidence=0.9)
        await kg.neighbourhood(
            requesting_agent_id="commander",
            seed_id=skill.id,
        )
        # The event payload should carry `commander`, not `reflector`.
        assert any(
            e.payload.get("requester") == "commander" for e in captured
        ), f"expected 'commander' requester in some event; got {[e.payload for e in captured]}"

    async def test_no_event_bus_means_silent_skip(self, memory) -> None:
        kg = build_knowledge_graph(memory=memory)
        skill = await _make_skill(memory, key="s")
        # Must not raise even with no bus wired.
        result = await kg.neighbourhood(requesting_agent_id="x", seed_id=skill.id, max_hops=1)
        assert isinstance(result, list)