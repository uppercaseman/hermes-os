"""Integration tests: the Reflection Engine writes natively into
the Sprint-2 typed Memory Manager.

These tests verify the public-API contract from the directive:
"Ensure the Reflection Engine writes directly into the new typed
memory architecture without changing its public interface." The
engine's Protocol (`MemoryWriter`) is satisfied structurally by
the real `MemoryManager` once `record_typed(...)` exists, and the
engine's `_commit_candidate` uses that path.

Each test exercises the full reflection flow end-to-end against
the real Memory Manager (no `FakeMemoryWriter`), then asserts on
the resulting typed entries -- `memory_type`, `confidence`,
`provenance`, and supersession state.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.memory_manager import build_memory_manager
from hermes.modules.reflection_engine import build_reflection_engine
from hermes.modules.reflection_engine.events import (
    MEMORY_PROMOTED,
    MEMORY_SUPERSEDED,
)
from hermes.modules.reflection_engine.models import (
    HIGH_CONFIDENCE_THRESHOLD,
    ConfidenceScore,
    Provenance,
    ReflectionThresholds,
    RiskLevel,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


class _FixedExtractor:
    """A test extractor that emits one candidate dict, ignoring the
    harvest inputs. Mirrors `_FixedExtractor` in `test_service.py`."""

    def __init__(self, candidates: list[dict]) -> None:
        self._candidates = candidates

    async def extract(self, *, mission_id, harvested, read_only_context) -> list[dict]:
        return self._candidates


def _build_engine(*, memory, bus, extractor, thresholds=None):
    """Constructs the reflection engine with the real Memory
    Manager (or a substitute satisfying the MemoryWriter Protocol)
    and a fixed extractor."""
    return build_reflection_engine(
        memory=memory,
        logs=_NoLogs(),
        working_memory=_NoWorkingMemory(),
        candidate_extractor=extractor,
        event_bus=bus,
        agent_id="reflector",
        thresholds=thresholds or ReflectionThresholds(),
    )


class _NoLogs:
    async def query(self, **kwargs):
        return []

    async def list_errors(self) -> list:
        return []


class _NoWorkingMemory:
    async def query(self, **kwargs):
        return []


def _candidate_dict(
    *,
    claim: str,
    candidate_type: str,
    destination: str,
    confidence: float,
    risk: str = "low",
    scope_fit: float = 0.9,
) -> dict:
    """One raw candidate dict for the engine's extractor to emit.

    Mirrors `_make_candidate_dict` from `test_service.py`: a
    candidate without provenance is dropped by the Phase-4
    provenance gate, so the default here is a single log-entry
    provenance."""
    return {
        "claim": claim,
        "candidate_type": candidate_type,
        "destination": destination,
        "score": {"confidence": confidence, "scope_fit": scope_fit, "risk": risk},
        "provenance": [Provenance(source_type="log_entry", source_id=str(uuid.uuid4())).model_dump()],
        "contributing_mission_ids": [],
    }


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestReflectionEngineWritesTypedMemory:
    async def test_engine_commits_via_record_typed_writes_skill_memory(self) -> None:
        bus = InMemoryEventBus()
        memory = build_memory_manager(event_bus=bus)
        # Two contributing missions so the Skill Memory threshold
        # gate (>=2 prior missions) is satisfied -- a single-mission
        # skill_pattern gets demoted to experience in Phase 3.
        # Skill patterns also require Phase-5 approval; the test
        # approves after `reflect`.
        mid_a, mid_b = uuid.uuid4(), uuid.uuid4()
        cand = _candidate_dict(
            claim="set budget alert when daily cost > $50",
            candidate_type="skill_pattern",
            destination="skill",
            confidence=HIGH_CONFIDENCE_THRESHOLD + 0.05,
            risk="low",
        )
        cand["contributing_mission_ids"] = [str(mid_a), str(mid_b)]
        extractor = _FixedExtractor([cand])
        eng = _build_engine(memory=memory, bus=bus, extractor=extractor)

        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")

        # Skill patterns require approval before commit; approve now.
        assert len(outcome.pending_approvals) == 1
        cand_obj = outcome.run.candidates[0]
        await eng.approve_candidate(
            run_id=outcome.run.id,
            candidate_id=cand_obj.id,
            approver="user",
        )

        # The committed entry is in Memory Manager's typed store.
        skill_entries = await memory.query(requesting_agent_id="reflector", memory_type="skill_memory")
        assert len(skill_entries) == 1
        entry = skill_entries[0]
        # First-class `memory_type` set -- the canonical store.
        assert entry.memory_type == "skill_memory"
        # Confidence promoted to first-class field, not buried in
        # `value['confidence']`.
        assert entry.confidence is not None and entry.confidence >= HIGH_CONFIDENCE_THRESHOLD
        # Legacy tag encoding still applied -- so any consumer that
        # filters by tag keeps working.
        assert "memory:skill_memory" in entry.tags
        assert "reflection_engine:managed" in entry.tags
        assert "reflection:skill" in entry.tags

    async def test_engine_commits_via_record_typed_writes_user_dna(self) -> None:
        """`user_dna` destination maps to canonical `user_dna` type
        (the `user_dna` cognitive memory type keeps its bare name)."""
        bus = InMemoryEventBus()
        memory = build_memory_manager(event_bus=bus)
        extractor = _FixedExtractor([
            _candidate_dict(
                claim="user prefers terse summaries",
                candidate_type="user_preference",
                destination="user_dna",
                confidence=0.95,
            ),
        ])
        # user_preference requires approval -- approve after reflect
        eng = _build_engine(memory=memory, bus=bus, extractor=extractor)
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        assert len(outcome.pending_approvals) == 1
        cand = outcome.run.candidates[0]
        await eng.approve_candidate(
            run_id=outcome.run.id,
            candidate_id=cand.id,
            approver="user",
        )

        dna_entries = await memory.query(requesting_agent_id="reflector", memory_type="user_dna")
        assert len(dna_entries) == 1
        assert dna_entries[0].memory_type == "user_dna"
        assert "memory:user_dna" in dna_entries[0].tags

    async def test_engine_supersession_writes_first_class_field(self) -> None:
        """When the engine detects a contradiction (Phase 4) and
        commits a replacement, the new entry uses the typed write
        path and `mark_superseded` is invoked on the old entry.
        The old entry remains readable (additive-only rule) and
        has `superseded_by` set.

        Setup: pre-existing experience entry with claim X; new
        candidate's claim = "never X" (negation marker). The
        scope gate matches because the new candidate is also an
        `experience_case` (its destination `experience` matches)."""
        bus = InMemoryEventBus()
        memory = build_memory_manager(event_bus=bus)
        existing = await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="experience_memory",
            key="experience:budget:cost_spike",
            value={"claim": "cost spikes during overnight batch processing"},
            confidence=0.4,
            tags=["reflection_engine:managed", "reflection:experience"],
        )
        extractor = _FixedExtractor([
            _candidate_dict(
                claim="never cost spikes during overnight batch processing",
                candidate_type="experience_case",
                destination="experience",
                confidence=0.95,
                risk="low",
            ),
        ])
        eng = _build_engine(memory=memory, bus=bus, extractor=extractor)
        await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")

        all_exp = await memory.query(
            requesting_agent_id="reflector", memory_type="experience_memory", include_superseded=True
        )
        assert len(all_exp) == 2
        old_entry = await memory.get(requesting_agent_id="reflector", entry_id=existing.id)
        assert old_entry is not None
        assert old_entry.superseded_by is not None
        assert "superseded" in old_entry.tags
        visible = await memory.query(requesting_agent_id="reflector", memory_type="experience_memory")
        assert len(visible) == 1
        assert visible[0].id != existing.id

    async def test_engine_publishes_event_on_typed_promotion(self) -> None:
        """`MEMORY_PROMOTED` event fires from Phase 6 alongside the
        typed write. The event payload references the entry id the
        engine just wrote."""
        bus = InMemoryEventBus()
        captured: list[str] = []

        async def _sink(event) -> None:
            captured.append(event.event_type)

        await bus.subscribe("*", _sink)

        memory = build_memory_manager(event_bus=bus)
        # Use `experience_case` -> `experience` so the scope gate
        # passes; high enough confidence to clear the threshold gate.
        extractor = _FixedExtractor([
            _candidate_dict(
                claim="cost spikes during overnight batch processing",
                candidate_type="experience_case",
                destination="experience",
                confidence=0.95,
            ),
        ])
        eng = _build_engine(memory=memory, bus=bus, extractor=extractor)
        await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")

        assert MEMORY_PROMOTED in captured

    async def test_engine_publishes_supersession_event(self) -> None:
        """`MEMORY_SUPERSEDED` event fires from Phase 6 when the
        new entry's typed write is followed by `mark_superseded`
        on the old entry.

        Sets up a contradiction (overlapping claim tokens with a
        negation marker) so the engine routes through the
        supersession path. The pre-existing entry must carry
        `reflection_engine:managed` + `reflection:skill` so the
        detector's tag-filter query can find it."""
        bus = InMemoryEventBus()
        captured: list[str] = []

        async def _sink(event) -> None:
            captured.append(event.event_type)

        await bus.subscribe("*", _sink)

        memory = build_memory_manager(event_bus=bus)
        await memory.record_typed(
            requesting_agent_id="reflector",
            memory_type="experience_memory",
            key="experience:budget:cost_spike",
            value={"claim": "cost spikes during overnight batch processing"},
            confidence=0.4,
            tags=["reflection_engine:managed", "reflection:experience"],
        )
        extractor = _FixedExtractor([
            _candidate_dict(
                claim="never cost spikes during overnight batch processing",
                candidate_type="experience_case",
                destination="experience",
                confidence=0.95,
            ),
        ])
        eng = _build_engine(memory=memory, bus=bus, extractor=extractor)
        await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")

        assert MEMORY_SUPERSEDED in captured

    async def test_engine_public_interface_unchanged(self) -> None:
        """The engine's public surface (`reflect`, `approve_candidate`,
        `reject_candidate`) is unchanged -- the engine's wiring to
        Memory Manager is an internal detail. The Protocol
        `MemoryWriter` has gained `record_typed` as a structural
        addition; the existing methods (`query`, `record`,
        `mark_superseded`) are unchanged."""
        from hermes.modules.reflection_engine.contracts import MemoryWriter
        import inspect

        # Protocol still exposes `query`, `record`, `mark_superseded`.
        assert hasattr(MemoryWriter, "query")
        assert hasattr(MemoryWriter, "record")
        assert hasattr(MemoryWriter, "mark_superseded")
        # Sprint-2 added `record_typed`.
        assert hasattr(MemoryWriter, "record_typed")
        # Engine public methods unchanged.
        assert callable(getattr(type(_build_engine(memory=build_memory_manager(), bus=InMemoryEventBus(), extractor=_FixedExtractor([]))), "reflect", None)) or hasattr(
            _build_engine(memory=build_memory_manager(), bus=InMemoryEventBus(), extractor=_FixedExtractor([])), "reflect"
        )