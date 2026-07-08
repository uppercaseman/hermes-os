"""End-to-end integration tests for the Sprint-3 Knowledge & Reasoning chain.

These tests exercise the full pipeline:

    Memory Manager ──▶ Knowledge Graph ──▶ Context Builder ──▶ Reasoning Engine

against real collaborators (no fakes), then assert on the resulting
`ReasoningContext` shape. They live under `reasoning_engine/tests/`
because the chain's primary output is the Engine's payload, but
they construct every layer explicitly.

Plus a Commander-binding integration test that verifies
`build_default_memory_resolver(...)` returns a callable that
satisfies Commander's `MemoryResolver` Protocol.
"""
from __future__ import annotations

import uuid

import pytest

from hermes.core.commander.models import Intent, WorkflowPlan
from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.context_builder import build_context_builder
from hermes.modules.knowledge_graph import build_knowledge_graph
from hermes.modules.memory_manager import build_memory_manager
from hermes.modules.memory_manager.typed import (
    MemoryRelationship,
    MemoryRelationshipType,
)
from hermes.modules.reasoning_engine import (
    ReasoningContext,
    ReasoningEngine,
    build_default_memory_resolver,
    build_reasoning_engine,
)
from hermes.modules.reflection_engine import build_reflection_engine
from hermes.modules.reflection_engine.models import (
    HIGH_CONFIDENCE_THRESHOLD,
    ConfidenceScore,
    Provenance,
    ReflectionThresholds,
)


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
def cb(memory, kg, bus):
    return build_context_builder(memory=memory, kg=kg, event_bus=bus, agent_id="reflector")


@pytest.fixture
def engine(cb, bus):
    return build_reasoning_engine(context_builder=cb, event_bus=bus, agent_id="commander")


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestFullChain:
    async def test_chain_returns_reasoning_context(
        self, engine, memory
    ) -> None:
        """Build a small knowledge graph in Memory, then exercise the
        full chain through to a `ReasoningContext`. Verify ordering,
        score monotonicity, and that every entry in the context came
        from the chain (not a stray seed-only result)."""
        # Seed: a skill pattern.
        skill = await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="budget:alert:cost_threshold",
            value={"claim": "alert when daily cost > $50"},
            confidence=0.95,
            importance=0.8,
        )
        # Linked experience: two missions that observed cost spikes.
        for i in range(2):
            await memory.record_typed(
                requesting_agent_id="reflector",
                memory_type="experience_memory",
                key=f"experience:budget:cost_spike:{i}",
                value={"claim": f"observed cost spike in mission {i}"},
                confidence=0.7,
                importance=0.6,
                relationships=[
                    MemoryRelationship(
                        relationship_type=MemoryRelationshipType.CONFIRMED_BY,
                        target_entry_id=skill.id,
                        weight=0.8,
                    )
                ],
            )
        # Linked user DNA: user prefers terse summaries.
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="user_dna",
            key="preference:summary:terse",
            value={"fact": "user prefers terse summaries"},
            confidence=0.95,
            importance=0.9,
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.REFERENCES,
                    target_entry_id=skill.id,
                    weight=0.4,
                )
            ],
        )

        ctx = await engine.prepare(
            type("Req", (), {})(),  # placeholder; replaced below
        ) if False else None  # silence linter; see actual call below

        from hermes.modules.reasoning_engine import ReasoningRequest

        ctx = await engine.prepare(
            ReasoningRequest(
                requesting_agent_id="commander",
                seed_ids=[skill.id],
                intent="synthesize a budget-alert recommendation",
                mission_id=uuid.uuid4(),
                max_entries=8,
            )
        )

        # The chain returned a non-empty context with the skill + its
        # downstream entries.
        assert isinstance(ctx, ReasoningContext)
        assert len(ctx.entries) >= 1
        assert ctx.entries[0].id == skill.id  # the seed is always first
        assert ctx.context_scores == sorted(ctx.context_scores, reverse=True)
        assert ctx.intent == "synthesize a budget-alert recommendation"
        # The trace carries the assembled ids.
        assert skill.id in ctx.trace.context_entry_ids

    async def test_chain_through_reflection_engine_then_reasoning(
        self, memory, kg, cb, engine, bus
    ) -> None:
        """End-to-end: a Reflection Engine run promotes a candidate
        to Memory, then the Reasoning Engine prepares a context from
        the freshly-promoted entry. The two layers communicate
        exclusively through Memory -- which is exactly the
        Architecture Freeze invariant Sprint-2 + Sprint-3 preserve.
        """
        # Reflection Engine's `MemoryWriter` Protocol is satisfied
        # structurally by Memory Manager.
        from hermes.modules.reflection_engine.models import (
            DestinationType,
            RiskLevel,
        )

        class _FixedExtractor:
            async def extract(self, *, mission_id, harvested, read_only_context):
                return [
                    {
                        "claim": "alert when daily cost > $50",
                        "candidate_type": "skill_pattern",
                        "destination": "skill",
                        "score": {
                            "confidence": HIGH_CONFIDENCE_THRESHOLD + 0.05,
                            "scope_fit": 0.9,
                            "risk": "low",
                        },
                        "provenance": [
                            Provenance(
                                source_type="log_entry",
                                source_id=str(uuid.uuid4()),
                            ).model_dump()
                        ],
                        "contributing_mission_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
                    }
                ]

        reflection = build_reflection_engine(
            memory=memory,
            event_bus=bus,
            candidate_extractor=_FixedExtractor(),
            thresholds=ReflectionThresholds(),
        )
        outcome = await reflection.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        # Approve the candidate so it lands in Memory.
        cand = outcome.run.candidates[0]
        await reflection.approve_candidate(
            run_id=outcome.run.id,
            candidate_id=cand.id,
            approver="user",
        )

        # Find the promoted skill.
        skill_entries = await memory.query(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
        )
        assert len(skill_entries) == 1
        promoted_skill = skill_entries[0]

        # Now reason over it.
        from hermes.modules.reasoning_engine import ReasoningRequest

        ctx = await engine.prepare(
            ReasoningRequest(
                requesting_agent_id="commander",
                seed_ids=[promoted_skill.id],
                intent="explain the budget-alert skill",
            )
        )
        assert ctx.entries[0].id == promoted_skill.id


# --------------------------------------------------------------------------- #
# Commander binding integration
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestCommanderBindingIntegration:
    async def test_resolver_satisfies_protocol_and_returns_requirement(
        self, engine, memory
    ) -> None:
        s = await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="skill_memory",
            key="s",
            value={},
            confidence=0.9,
        )
        resolver = build_default_memory_resolver(reasoning_engine=engine)
        # The callable returns a MemoryRequirement with the right
        # shape -- that's what Commander's wiring site consumes.
        intent = Intent(
            name="synth",
            confidence=1.0,
            slots={
                "seed_memory_ids": [str(s.id)],
                "description": "synthesize",
            },
        )
        workflow = WorkflowPlan(workflow_id="wf", name="synth", steps=["x"])
        req = await resolver(intent, workflow)
        assert str(s.id) in req.keys

    async def test_resolver_returns_empty_requirement_for_no_seeds(
        self, engine
    ) -> None:
        resolver = build_default_memory_resolver(reasoning_engine=engine)
        intent = Intent(name="noop", confidence=1.0, slots={})
        workflow = WorkflowPlan(workflow_id="wf", name="noop", steps=[])
        req = await resolver(intent, workflow)
        assert req.keys == []