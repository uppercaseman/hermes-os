"""Tests for the Context Builder runtime.

End-to-end against the real `MemoryManager` + `KnowledgeGraph`
(no fakes for either). Each test exercises the assembly pipeline
and asserts on the resulting `AssembledContext`'s entries, scoring
trace, ordering, and metadata.
"""
from __future__ import annotations

import uuid

import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.context_builder import (
    AssembledContext,
    CONTEXT_BUILT,
    CONTEXT_BUILD_FAILED,
    ContextBuilder,
    ContextRequest,
    ContextScoreEntry,
    EmptyContextError,
    build_context_builder,
)
from hermes.modules.knowledge_graph import build_knowledge_graph
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


@pytest.fixture
def builder(memory, kg, bus):
    return build_context_builder(
        memory=memory, kg=kg, event_bus=bus, agent_id="reflector"
    )


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


async def _capture(bus):
    """Return an event-type-capture list AND a sink coroutine so the
    test can subscribe to the bus before the call under test runs."""
    captured = []

    async def _sink(event):
        captured.append(event.event_type)

    await bus.subscribe("*", _sink)
    return captured


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


class TestConstruction:
    def test_build_factory_returns_context_builder(self, memory, kg) -> None:
        cb = build_context_builder(memory=memory, kg=kg)
        assert isinstance(cb, ContextBuilder)

    def test_factory_requires_memory_and_kg(self) -> None:
        with pytest.raises(TypeError):
            build_context_builder()  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


class TestValidation:
    async def test_empty_seed_set_raises_config_error(self, builder) -> None:
        with pytest.raises(Exception):
            await builder.assemble(
                ContextRequest(requesting_agent_id="reflector", seed_ids=[])
            )

    async def test_k_must_be_positive(self, builder) -> None:
        with pytest.raises(Exception):
            await builder.assemble(
                ContextRequest(requesting_agent_id="reflector", seed_ids=[uuid.uuid4()], k=0)
            )

    async def test_min_confidence_out_of_range(self, builder) -> None:
        with pytest.raises(Exception):
            await builder.assemble(
                ContextRequest(
                    requesting_agent_id="reflector",
                    seed_ids=[uuid.uuid4()],
                    min_confidence=1.5,
                )
            )


# --------------------------------------------------------------------------- #
# Assembly -- basic shape
# --------------------------------------------------------------------------- #


class TestAssemblyShape:
    async def test_returns_assembled_context_with_seeds_only(
        self, builder, memory
    ) -> None:
        s1 = await _make_skill(memory, key="s1", confidence=0.9)
        s2 = await _make_skill(memory, key="s2", confidence=0.7)
        ctx = await builder.assemble(
            ContextRequest(
                requesting_agent_id="reflector",
                seed_ids=[s1.id, s2.id],
                k=4,
            )
        )
        assert isinstance(ctx, AssembledContext)
        assert {e.id for e in ctx.entries} == {s1.id, s2.id}
        assert all(isinstance(t, ContextScoreEntry) for t in ctx.scoring_trace)
        assert len(ctx.scoring_trace) == 2

    async def test_results_ordered_by_score_descending(
        self, builder, memory
    ) -> None:
        high = await _make_skill(memory, key="high", confidence=0.95)
        mid = await _make_skill(memory, key="mid", confidence=0.7)
        low = await _make_skill(memory, key="low", confidence=0.4)
        ctx = await builder.assemble(
            ContextRequest(
                requesting_agent_id="reflector",
                seed_ids=[high.id, mid.id, low.id],
                k=3,
            )
        )
        scores = [t.score for t in ctx.scoring_trace]
        assert scores == sorted(scores, reverse=True)

    async def test_k_caps_results(self, builder, memory) -> None:
        seeds = [await _make_skill(memory, key=f"s{i}", confidence=0.9) for i in range(5)]
        ctx = await builder.assemble(
            ContextRequest(
                requesting_agent_id="reflector",
                seed_ids=[s.id for s in seeds],
                k=2,
            )
        )
        assert len(ctx.entries) == 2

    async def test_metadata_includes_seed_and_candidate_counts(
        self, builder, memory
    ) -> None:
        s = await _make_skill(memory, key="s", confidence=0.9)
        ctx = await builder.assemble(
            ContextRequest(
                requesting_agent_id="reflector",
                seed_ids=[s.id],
                k=4,
            )
        )
        assert ctx.metadata["seed_count"] == "1"
        assert ctx.metadata["requester"] == "reflector"


# --------------------------------------------------------------------------- #
# Assembly -- KG-driven expansion
# --------------------------------------------------------------------------- #


class TestAssemblyWithGraph:
    async def test_includes_neighbours_of_seeds(self, builder, memory) -> None:
        seed = await _make_skill(memory, key="seed", confidence=0.9)
        neighbour = await _make_experience(memory, key="nbr", confidence=0.8)
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="seed",
            value={"claim": "seed"},
            confidence=0.9,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.DERIVED_FROM,
                    target_entry_id=neighbour.id,
                    weight=1.0,
                )
            ],
        )
        ctx = await builder.assemble(
            ContextRequest(
                requesting_agent_id="reflector",
                seed_ids=[seed.id],
                k=8,
            )
        )
        ids = {e.id for e in ctx.entries}
        assert seed.id in ids
        assert neighbour.id in ids

    async def test_publishes_context_built_event(self, builder, memory, bus) -> None:
        events = await _capture(bus)
        s = await _make_skill(memory, key="s")
        await builder.assemble(
            ContextRequest(requesting_agent_id="reflector", seed_ids=[s.id])
        )
        assert CONTEXT_BUILT in events

    async def test_unresolvable_seeds_publishes_failure(self, builder, bus) -> None:
        events = await _capture(bus)
        with pytest.raises(EmptyContextError):
            await builder.assemble(
                ContextRequest(
                    requesting_agent_id="reflector",
                    seed_ids=[uuid.uuid4(), uuid.uuid4()],
                )
            )
        assert CONTEXT_BUILD_FAILED in events


# --------------------------------------------------------------------------- #
# Assembly -- scoring trace
# --------------------------------------------------------------------------- #


class TestScoringTrace:
    async def test_trace_has_method_per_entry(self, builder, memory) -> None:
        seed = await _make_skill(memory, key="seed", confidence=0.9)
        neighbour = await _make_experience(memory, key="nbr", confidence=0.8)
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="seed",
            value={"claim": "seed"},
            confidence=0.9,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.REFERENCES,
                    target_entry_id=neighbour.id,
                    weight=1.0,
                )
            ],
        )
        ctx = await builder.assemble(
            ContextRequest(requesting_agent_id="reflector", seed_ids=[seed.id])
        )
        trace_by_id = {t.entry_id: t for t in ctx.scoring_trace}
        assert trace_by_id[seed.id].method == "direct_seed"
        assert trace_by_id[seed.id].distance == 0
        assert trace_by_id[neighbour.id].method in ("neighbour", "expansion")
        assert trace_by_id[neighbour.id].distance >= 1

    async def test_scores_in_unit_interval(self, builder, memory) -> None:
        s = await _make_skill(memory, key="s")
        ctx = await builder.assemble(
            ContextRequest(requesting_agent_id="reflector", seed_ids=[s.id])
        )
        for t in ctx.scoring_trace:
            assert 0.0 <= t.score <= 1.0


# --------------------------------------------------------------------------- #
# Cross-cutting
# --------------------------------------------------------------------------- #


class TestCrossCutting:
    async def test_no_event_bus_means_silent_skip(self, memory, kg) -> None:
        cb = build_context_builder(memory=memory, kg=kg)
        s = await _make_skill(memory, key="s")
        # Must not raise even with no bus wired.
        ctx = await cb.assemble(
            ContextRequest(requesting_agent_id="reflector", seed_ids=[s.id])
        )
        assert len(ctx.entries) >= 1

    async def test_assembly_is_idempotent(self, builder, memory) -> None:
        s = await _make_skill(memory, key="s", confidence=0.9)
        req = ContextRequest(
            requesting_agent_id="reflector",
            seed_ids=[s.id],
            k=4,
        )
        a = await builder.assemble(req)
        b = await builder.assemble(req)
        assert [e.id for e in a.entries] == [e.id for e in b.entries]
        assert [t.score for t in a.scoring_trace] == [t.score for t in b.scoring_trace]

    async def test_min_confidence_excludes_low_confidence_entries(
        self, builder, memory
    ) -> None:
        seed = await _make_skill(memory, key="seed", confidence=0.9)
        low = await _make_experience(memory, key="low", confidence=0.2)
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="seed",
            value={"claim": "seed"},
            confidence=0.9,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.REFERENCES,
                    target_entry_id=low.id,
                    weight=1.0,
                )
            ],
        )
        ctx = await builder.assemble(
            ContextRequest(
                requesting_agent_id="reflector",
                seed_ids=[seed.id],
                min_confidence=0.5,
                k=8,
            )
        )
        ids = {e.id for e in ctx.entries}
        assert seed.id in ids
        assert low.id not in ids

    async def test_mission_id_passes_through_to_metadata(
        self, builder, memory
    ) -> None:
        s = await _make_skill(memory, key="s")
        mid = uuid.uuid4()
        ctx = await builder.assemble(
            ContextRequest(
                requesting_agent_id="reflector",
                seed_ids=[s.id],
                mission_id=mid,
            )
        )
        assert ctx.metadata["mission_id"] == str(mid)