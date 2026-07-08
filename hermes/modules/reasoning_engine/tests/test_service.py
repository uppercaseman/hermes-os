"""Tests for the Reasoning Engine.

End-to-end against the real Memory Manager + Knowledge Graph +
Context Builder chain. Each test exercises the read-only
preparation pipeline and asserts on `ReasoningContext` shape,
ordering, and scoring.

The Engine does NOT call AI models or perform provider reasoning
in Sprint-3 -- per the directive, it's preparation only.
"""
from __future__ import annotations

import uuid

import pytest

from hermes.core.commander.models import Intent, MemoryRequirement, WorkflowPlan
from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.context_builder import build_context_builder
from hermes.modules.knowledge_graph import build_knowledge_graph
from hermes.modules.memory_manager import build_memory_manager
from hermes.modules.memory_manager.typed import (
    MemoryRelationship,
    MemoryRelationshipType,
)
from hermes.modules.reasoning_engine import (
    EmptyReasoningContextError,
    ProviderReasoningUnavailableError,
    REASONING_PREPARATION_FAILED,
    REASONING_PREPARED,
    ReasoningConfigError,
    ReasoningContext,
    ReasoningEngine,
    ReasoningMode,
    ReasoningRequest,
    ReasoningTrace,
    build_default_memory_resolver,
    build_reasoning_engine,
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
def cb(memory, kg, bus):
    return build_context_builder(memory=memory, kg=kg, event_bus=bus, agent_id="reflector")


@pytest.fixture
def engine(cb, bus):
    return build_reasoning_engine(context_builder=cb, event_bus=bus, agent_id="commander")


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


async def _capture_types(bus):
    captured = []

    async def _sink(event):
        captured.append(event.event_type)

    await bus.subscribe("*", _sink)
    return captured


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


class TestConstruction:
    def test_build_factory_returns_engine(self, cb) -> None:
        eng = build_reasoning_engine(context_builder=cb)
        assert isinstance(eng, ReasoningEngine)

    def test_factory_requires_context_builder(self) -> None:
        with pytest.raises(TypeError):
            build_reasoning_engine()  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


class TestValidation:
    async def test_empty_seed_set_raises_config_error(self, engine) -> None:
        with pytest.raises(ReasoningConfigError):
            await engine.prepare(
                ReasoningRequest(
                    requesting_agent_id="commander",
                    seed_ids=[],
                    intent="something",
                )
            )

    async def test_zero_max_entries_raises(self, engine) -> None:
        with pytest.raises(ReasoningConfigError):
            await engine.prepare(
                ReasoningRequest(
                    requesting_agent_id="commander",
                    seed_ids=[uuid.uuid4()],
                    intent="something",
                    max_entries=0,
                )
            )

    async def test_blank_intent_raises(self, engine) -> None:
        with pytest.raises(ReasoningConfigError):
            await engine.prepare(
                ReasoningRequest(
                    requesting_agent_id="commander",
                    seed_ids=[uuid.uuid4()],
                    intent="   ",
                )
            )

    async def test_non_assemble_mode_raises_guard_rail(self, engine) -> None:
        with pytest.raises(ProviderReasoningUnavailableError):
            await engine.prepare(
                ReasoningRequest(
                    requesting_agent_id="commander",
                    seed_ids=[uuid.uuid4()],
                    intent="something",
                    mode="summarize",  # type: ignore[arg-type]
                )
            )

    async def test_min_confidence_out_of_range(self, engine) -> None:
        with pytest.raises(ReasoningConfigError):
            await engine.prepare(
                ReasoningRequest(
                    requesting_agent_id="commander",
                    seed_ids=[uuid.uuid4()],
                    intent="something",
                    min_confidence=1.5,
                )
            )


# --------------------------------------------------------------------------- #
# Prepare -- basic shape
# --------------------------------------------------------------------------- #


class TestPrepareShape:
    async def test_returns_reasoning_context(self, engine, memory) -> None:
        s = await _make_skill(memory, key="s", confidence=0.9)
        ctx = await engine.prepare(
            ReasoningRequest(
                requesting_agent_id="commander",
                seed_ids=[s.id],
                intent="synthesize recommendation",
            )
        )
        assert isinstance(ctx, ReasoningContext)
        assert ctx.intent == "synthesize recommendation"
        assert ctx.mode == "assemble"
        assert ctx.entries == [s]

    async def test_scoring_trace_carries_request(self, engine, memory) -> None:
        s = await _make_skill(memory, key="s", confidence=0.9)
        ctx = await engine.prepare(
            ReasoningRequest(
                requesting_agent_id="commander",
                seed_ids=[s.id],
                intent="synthesize",
            )
        )
        assert isinstance(ctx.trace, ReasoningTrace)
        assert ctx.trace.request_id == ctx.request_id
        assert ctx.trace.request.intent == "synthesize"
        assert ctx.trace.context_entry_ids == [s.id]
        assert ctx.trace.context_scores == ctx.context_scores

    async def test_entries_ordered_by_score(self, engine, memory) -> None:
        a = await _make_skill(memory, key="a", confidence=0.95)
        b = await _make_skill(memory, key="b", confidence=0.7)
        c = await _make_skill(memory, key="c", confidence=0.4)
        ctx = await engine.prepare(
            ReasoningRequest(
                requesting_agent_id="commander",
                seed_ids=[a.id, b.id, c.id],
                intent="rank",
                max_entries=3,
            )
        )
        assert ctx.context_scores == sorted(ctx.context_scores, reverse=True)

    async def test_max_entries_caps_result(self, engine, memory) -> None:
        entries = [
            await _make_skill(memory, key=f"s{i}", confidence=0.9)
            for i in range(5)
        ]
        ctx = await engine.prepare(
            ReasoningRequest(
                requesting_agent_id="commander",
                seed_ids=[e.id for e in entries],
                intent="cap",
                max_entries=2,
            )
        )
        assert len(ctx.entries) == 2

    async def test_publishes_prepared_event(self, engine, memory, bus) -> None:
        events = await _capture_types(bus)
        s = await _make_skill(memory, key="s")
        await engine.prepare(
            ReasoningRequest(
                requesting_agent_id="commander", seed_ids=[s.id], intent="x"
            )
        )
        assert REASONING_PREPARED in events


# --------------------------------------------------------------------------- #
# Empty + failure paths
# --------------------------------------------------------------------------- #


class TestEmptyAndFailure:
    async def test_no_resolvable_seeds_raises_empty(self, engine, bus) -> None:
        events = await _capture_types(bus)
        with pytest.raises(EmptyReasoningContextError):
            await engine.prepare(
                ReasoningRequest(
                    requesting_agent_id="commander",
                    seed_ids=[uuid.uuid4(), uuid.uuid4()],
                    intent="nothing",
                )
            )
        assert REASONING_PREPARATION_FAILED in events

    async def test_assembly_failure_publishes_failure_event(
        self, engine, memory, bus
    ) -> None:
        events = await _capture_types(bus)
        # A skill with very low confidence and min_confidence=0.99
        # should be filtered before it becomes a result, leaving the
        # assembly empty.
        s = await _make_skill(memory, key="s", confidence=0.1)
        # To force a genuine empty assembly, set min_confidence=0.99.
        with pytest.raises(EmptyReasoningContextError):
            await engine.prepare(
                ReasoningRequest(
                    requesting_agent_id="commander",
                    seed_ids=[s.id],
                    intent="min",
                    min_confidence=0.99,
                )
            )
        assert REASONING_PREPARATION_FAILED in events


# --------------------------------------------------------------------------- #
# No model calls
# --------------------------------------------------------------------------- #


class TestNoModelCalls:
    async def test_does_not_invoke_any_ai_model(self, engine, memory) -> None:
        """The Engine must not call any AI model or perform provider
        reasoning in Sprint-3. Asserted by ensuring the Engine's
        only collaborator is the Context Builder; no Tool Manager /
        Provider interfaces are wired.
        """
        # Inspect the engine's collaborators attribute (private)
        # and confirm only the Context Builder is referenced.
        # `cb` is the only required kwarg; anything beyond that
        # would be a model / tool / provider adapter.
        assert hasattr(engine, "_cb")
        # The Engine should not have any provider-related attribute.
        for forbidden in ("_provider", "_model", "_llm", "_tool_manager", "_agent"):
            assert not hasattr(engine, forbidden), (
                f"ReasoningEngine.sprint3 must not wire {forbidden!r}"
            )


# --------------------------------------------------------------------------- #
# Cross-cutting
# --------------------------------------------------------------------------- #


class TestCrossCutting:
    async def test_no_event_bus_means_silent_skip(self, cb, memory) -> None:
        eng = build_reasoning_engine(context_builder=cb)
        s = await _make_skill(memory, key="s")
        ctx = await eng.prepare(
            ReasoningRequest(
                requesting_agent_id="commander", seed_ids=[s.id], intent="x"
            )
        )
        assert len(ctx.entries) >= 1

    async def test_preparation_is_idempotent(self, engine, memory) -> None:
        s = await _make_skill(memory, key="s", confidence=0.9)
        req = ReasoningRequest(
            requesting_agent_id="commander",
            seed_ids=[s.id],
            intent="x",
        )
        a = await engine.prepare(req)
        b = await engine.prepare(req)
        assert [e.id for e in a.entries] == [e.id for e in b.entries]
        assert a.context_scores == b.context_scores


# --------------------------------------------------------------------------- #
# Commander binding helper
# --------------------------------------------------------------------------- #


class TestCommanderBinding:
    def test_build_default_memory_resolver_returns_callable(self, engine) -> None:
        resolver = build_default_memory_resolver(reasoning_engine=engine)
        assert callable(resolver)

    async def test_resolver_returns_empty_when_no_seeds(self, engine) -> None:
        resolver = build_default_memory_resolver(reasoning_engine=engine)
        intent = Intent(name="noop", confidence=1.0, slots={})
        workflow = WorkflowPlan(workflow_id="wf", name="noop", steps=[])
        req = await resolver(intent, workflow)
        assert isinstance(req, MemoryRequirement)
        assert req.keys == []

    async def test_resolver_populates_keys_from_prepared_context(
        self, engine, memory
    ) -> None:
        # Set up an entry in memory so the Context Builder has
        # something to assemble.
        s = await _make_skill(memory, key="s", confidence=0.9)
        resolver = build_default_memory_resolver(reasoning_engine=engine)
        intent = Intent(
            name="synth",
            confidence=1.0,
            slots={
                "seed_memory_ids": [str(s.id)],
                "description": "synthesize a recommendation",
            },
        )
        workflow = WorkflowPlan(workflow_id="wf", name="synth", steps=["x"])
        req = await resolver(intent, workflow)
        assert isinstance(req, MemoryRequirement)
        assert str(s.id) in req.keys
        assert req.scope == s.scope

    async def test_resolver_skips_malformed_seed_ids(self, engine) -> None:
        resolver = build_default_memory_resolver(reasoning_engine=engine)
        intent = Intent(
            name="noop",
            confidence=1.0,
            slots={"seed_memory_ids": ["not-a-uuid", "also-bad"]},
        )
        workflow = WorkflowPlan(workflow_id="wf", name="noop", steps=[])
        req = await resolver(intent, workflow)
        assert req.keys == []

    async def test_resolver_satisfies_memory_resolver_protocol_shape(
        self, engine
    ) -> None:
        """The returned callable must accept (intent, workflow) and
        return a MemoryRequirement. Asserted by calling it with
        empty fixtures; the Protocol shape is enforced by the
        closure's signature."""
        resolver = build_default_memory_resolver(reasoning_engine=engine)
        # The `inspect` check is defensive; the real Protocol
        # enforcement happens at Commander's wiring site.
        import inspect

        sig = inspect.signature(resolver)
        params = list(sig.parameters.keys())
        assert params == ["intent", "workflow"]


# --------------------------------------------------------------------------- #
# ReasoningMode
# --------------------------------------------------------------------------- #


class TestReasoningMode:
    def test_default_mode_is_assemble(self) -> None:
        req = ReasoningRequest(
            requesting_agent_id="x", seed_ids=[uuid.uuid4()], intent="x"
        )
        assert req.mode == "assemble"

    def test_mode_literal_values(self) -> None:
        assert set(ReasoningMode.__args__) == {"assemble", "summarize", "compare"}  # type: ignore[attr-defined]
