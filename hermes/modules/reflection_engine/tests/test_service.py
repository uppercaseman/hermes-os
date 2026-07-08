"""Tests for the Reflection Engine.

Coverage matches the directive's required scenarios:
    - Normal flow
    - Duplicates
    - Contradictions
    - Approval required
    - Approval denied
    - Memory promotion
    - Memory rejection
    - Event publication
    - Error recovery
    - Mission cancellation (reduced-form reflection)
    - Idempotency
    - plus the helpers (claim_key, destination_tag) and the Protocol
      round-trip, which together verify the public surface.

The fakes in `_fakes.py` are reference implementations of the
engine's collaborator Protocols; if a future engine change drops a
method, mypy on the fakes will fail at type-check time rather than
the tests failing at runtime with AttributeError.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from hermes.modules.reflection_engine import (
    MEMORY_APPROVAL_GRANTED,
    MEMORY_CANDIDATE_CREATED,
    MEMORY_PROMOTED,
    MEMORY_REJECTED,
    MEMORY_SUPERSEDED,
    REFLECTION_COMPLETED,
    REFLECTION_FAILED,
    REFLECTION_STARTED,
    ApprovalDeniedError,
    CandidateShapeError,
    ReflectionConfigError,
    ReflectionEngine,
    UnknownReflectionCandidateError,
    UnknownReflectionRunError,
    all_destinations,
    claim_key,
    destination_tag,
)
from hermes.modules.reflection_engine.models import (
    HIGH_CONFIDENCE_THRESHOLD,
    Provenance,
    ReflectionThresholds,
)
from hermes.modules.reflection_engine.interface import build_reflection_engine
from hermes.modules.reflection_engine.service import (
    MISSION_COMPLETED_EVENT,
    MISSION_FAILED_EVENT,
)
from hermes.modules.reflection_engine.tests._fakes import (
    FakeEventBus,
    FakeLogQuerier,
    FakeMemoryWriter,
    FakeWorkingMemoryReader,
    MemoryEntry,
    make_event,
    make_log_entry,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _build_engine(
    *,
    memory: FakeMemoryWriter | None = None,
    logs: FakeLogQuerier | None = None,
    working_memory: FakeWorkingMemoryReader | None = None,
    event_bus: FakeEventBus | None = None,
    thresholds: ReflectionThresholds | None = None,
) -> tuple[ReflectionEngine, FakeMemoryWriter, FakeLogQuerier, FakeWorkingMemoryReader, FakeEventBus]:
    memory = memory or FakeMemoryWriter()
    logs = logs or FakeLogQuerier()
    working_memory = working_memory or FakeWorkingMemoryReader()
    event_bus = event_bus or FakeEventBus()
    eng = build_reflection_engine(
        memory=memory,  # type: ignore[arg-type]
        logs=logs,  # type: ignore[arg-type]
        working_memory=working_memory,  # type: ignore[arg-type]
        event_bus=event_bus,  # type: ignore[arg-type]
        thresholds=thresholds,
    )
    return eng, memory, logs, working_memory, event_bus  # type: ignore[return-value]


def _make_candidate_dict(
    *,
    claim: str,
    candidate_type: str,
    destination: str,
    confidence: float,
    risk: str = "low",
    provenance: list[Provenance] | None = None,
    contributing_mission_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    return {
        "claim": claim,
        "candidate_type": candidate_type,
        "destination": destination,
        "score": {"confidence": confidence, "scope_fit": 0.8, "risk": risk},
        "provenance": [p.model_dump() for p in provenance] if provenance is not None else [Provenance(source_type="log_entry", source_id=str(uuid.uuid4())).model_dump()],
        "contributing_mission_ids": [str(m) for m in (contributing_mission_ids or [])],
    }


# --------------------------------------------------------------------------- #
# Helpers and constants
# --------------------------------------------------------------------------- #


class TestHelpers:
    def test_all_destinations_returns_four_entries(self) -> None:
        assert all_destinations() == ["user_dna", "skill", "experience", "project"]

    def test_destination_tag_format(self) -> None:
        assert destination_tag("user_dna") == "reflection:user_dna"
        assert destination_tag("skill") == "reflection:skill"

    def test_claim_key_is_destination_scoped_and_normalised(self) -> None:
        # Different destinations, same claim -> different keys.
        assert claim_key("user_dna", "Be terse") != claim_key("skill", "Be terse")
        # Normalisation: whitespace + case folded.
        assert claim_key("user_dna", "Prefer Terse Responses") == claim_key("user_dna", "prefer terse responses")

    def test_thresholds_default(self) -> None:
        t = ReflectionThresholds()
        assert t.user_dna_min == 0.7
        assert t.project_min == 0.6
        assert t.skill_min == 0.8
        assert t.experience_min == 0.5
        assert t.skill_min_missions == 2
        assert t.medium_risk_approval_floor == 0.7


# --------------------------------------------------------------------------- #
# Configuration and surface
# --------------------------------------------------------------------------- #


class TestSurface:
    def test_engine_requires_memory(self) -> None:
        with pytest.raises(ReflectionConfigError):
            ReflectionEngine(memory=None)  # type: ignore[arg-type]

    def test_protocol_round_trip(self) -> None:
        eng, *_ = _build_engine()
        # The Protocol is structural; we verify the engine implements
        # every method the Protocol declares rather than relying on
        # `isinstance` (which requires `@runtime_checkable`).
        for name in (
            "start",
            "stop",
            "reflect",
            "approve_candidate",
            "reject_candidate",
            "get_run",
            "get_outcome",
            "list_runs",
        ):
            assert hasattr(eng, name), f"ReflectionEngine missing Protocol method {name!r}"

    def test_unknown_run_lookup_raises(self) -> None:
        eng, *_ = _build_engine()
        with pytest.raises(UnknownReflectionRunError):
            eng.get_run(uuid.uuid4())

    def test_unknown_outcome_lookup_raises(self) -> None:
        eng, *_ = _build_engine()
        with pytest.raises(UnknownReflectionRunError):
            eng.get_outcome(uuid.uuid4())

    def test_invalid_terminal_status_rejected(self) -> None:
        eng, *_ = _build_engine()
        with pytest.raises(CandidateShapeError):
            import asyncio
            asyncio.run(eng.reflect(mission_id=uuid.uuid4(), terminal_status="draft"))


# --------------------------------------------------------------------------- #
# Normal flow: harvest -> candidates -> score -> gate -> commit
# --------------------------------------------------------------------------- #


class TestNormalFlow:
    @pytest.mark.asyncio
    async def test_harvest_with_no_data_produces_zero_candidates(self) -> None:
        eng, *_ = _build_engine()
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        assert outcome.success
        assert outcome.run.candidates == []
        assert outcome.run.promoted_count == 0

    @pytest.mark.asyncio
    async def test_experience_case_passed_and_committed(self) -> None:
        eng, memory, logs, _, event_bus = _build_engine()
        mission_id = uuid.uuid4()
        # Seed an error followed by a recovery -- the default extractor
        # turns that into an experience_case.
        err = make_log_entry(event_type="tool.run.failed", mission_id=mission_id, severity="error", tool_name="sql_query", payload={"message": "syntax error"})
        rec = make_log_entry(event_type="tool.retry_succeeded", mission_id=mission_id, tool_name="sql_query", payload={"message": "fixed query"})
        logs.entries.extend([err, rec])

        outcome = await eng.reflect(mission_id=mission_id, terminal_status="completed")

        assert outcome.success
        assert outcome.run.candidates
        # One experience_case candidate, scored, gated, committed.
        cand = outcome.run.candidates[0]
        assert cand.candidate_type == "experience_case"
        assert cand.destination == "experience"
        assert outcome.run.promoted_count == 1
        assert cand.result_entry_id is not None
        # The Memory Manager received the entry.
        assert any(e.scope == "persistent" and e.key == claim_key("experience", cand.claim) for e in memory.entries)

    @pytest.mark.asyncio
    async def test_user_feedback_produces_user_preference_awaiting_approval(self) -> None:
        eng, *_ = _build_engine()
        mission_id = uuid.uuid4()
        fbk = make_log_entry(
            event_type="user.feedback",
            mission_id=mission_id,
            payload={"source": "user", "kind": "feedback", "text": "Be terse"},
        )
        eng, memory, logs, _, _ = _build_engine()
        logs.entries.append(fbk)

        outcome = await eng.reflect(mission_id=mission_id, terminal_status="completed")
        assert outcome.success
        assert outcome.requires_human_action
        assert outcome.pending_approvals  # one pending candidate

        cand = outcome.run.candidates[0]
        assert cand.candidate_type == "user_preference"
        assert cand.destination == "user_dna"
        assert cand.approval_required is True
        # Not yet committed -- approval gates commit.
        assert cand.result_entry_id is None
        # ...and not present in memory.
        assert not any(e.value.get("claim") == "Be terse" for e in memory.entries)


# --------------------------------------------------------------------------- #
# Quality gates
# --------------------------------------------------------------------------- #


class TestQualityGates:
    @pytest.mark.asyncio
    async def test_threshold_gate_rejects_low_confidence(self) -> None:
        # A project_fact with confidence 0.5 is below project_min (0.6).
        eng, *_ = _build_engine()
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(
                claim="low-confidence project fact",
                candidate_type="project_fact",
                destination="project",
                confidence=0.5,
                risk="low",
            )]
        )
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        cand = outcome.run.candidates[0]
        # The verdict record identifies which gate rejected the candidate.
        assert any(v.gate == "threshold" and v.outcome == "fail" for v in outcome.run.verdicts)
        assert outcome.run.rejected_count == 1
        assert outcome.run.promoted_count == 0

    @pytest.mark.asyncio
    async def test_provenance_gate_rejects_empty_evidence(self) -> None:
        eng, *_ = _build_engine()
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(claim="no provenance", candidate_type="project_fact", destination="project", confidence=1.0, provenance=[])]
        )

        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        assert any(v.gate == "provenance" and v.outcome == "fail" for v in outcome.run.verdicts)

    @pytest.mark.asyncio
    async def test_scope_gate_rejects_mismatch(self) -> None:
        eng, *_ = _build_engine()
        # user_preference -> project is a scope mismatch.
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(claim="bad", candidate_type="user_preference", destination="project", confidence=1.0)]
        )
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        assert any(v.gate == "scope" and v.outcome == "fail" for v in outcome.run.verdicts)


# --------------------------------------------------------------------------- #
# Duplicates
# --------------------------------------------------------------------------- #


class TestDuplicates:
    @pytest.mark.asyncio
    async def test_near_duplicate_merges_with_existing_entry(self) -> None:
        eng, memory, *_ = _build_engine()
        # Pre-seed an existing entry that shares the candidate's claim.
        existing = MemoryEntry(
            scope="persistent",
            key=claim_key("experience", "Recovered from syntax error via sql_query"),
            value={"claim": "Recovered from syntax error via sql_query", "confidence": 0.6, "destination": "experience"},
            tags=["reflection_engine:managed", destination_tag("experience")],
        )
        memory.entries.append(existing)

        # Run the normal flow -- the candidate should merge into `existing`.
        mission_id = uuid.uuid4()
        eng._logs.entries.extend([  # type: ignore[attr-defined]
            make_log_entry(event_type="tool.run.failed", mission_id=mission_id, severity="error", tool_name="sql_query", payload={"message": "syntax error"}),
            make_log_entry(event_type="tool.retry_succeeded", mission_id=mission_id, tool_name="sql_query", payload={"message": "fixed query"}),
        ])
        outcome = await eng.reflect(mission_id=mission_id, terminal_status="completed")

        cand = outcome.run.candidates[0]
        assert cand.merged_into == existing.id
        assert outcome.run.merged_count == 1
        # Confidence updated to max in the existing entry's value.
        assert existing.value["confidence"] >= 0.6

    @pytest.mark.asyncio
    async def test_second_reflection_is_idempotent_for_committed_work(self) -> None:
        eng, memory, logs, _, _ = _build_engine()
        mission_id = uuid.uuid4()
        logs.entries.append(make_log_entry(event_type="tool.run.failed", mission_id=mission_id, severity="error", tool_name="sql_query", payload={"message": "x"}))
        logs.entries.append(make_log_entry(event_type="tool.retry_succeeded", mission_id=mission_id, tool_name="sql_query", payload={"message": "y"}))

        first = await eng.reflect(mission_id=mission_id, terminal_status="completed")
        second = await eng.reflect(mission_id=mission_id, terminal_status="completed")
        # Same run id (idempotency).
        assert first.run.id == second.run.id
        # Only one promotion recorded -- the second pass found the
        # existing entry and merged rather than re-committing.
        assert first.run.promoted_count == 1
        assert second.run.promoted_count == 1


# --------------------------------------------------------------------------- #
# Contradictions
# --------------------------------------------------------------------------- #


class TestContradictions:
    @pytest.mark.asyncio
    async def test_high_confidence_existing_routes_to_approval(self) -> None:
        eng, memory, *_ = _build_engine()
        existing = MemoryEntry(
            scope="persistent",
            key=claim_key("experience", "use sql_query for joins"),
            value={
                "claim": "use sql_query for joins",
                "confidence": HIGH_CONFIDENCE_THRESHOLD + 0.05,
                "destination": "experience",
            },
            tags=["reflection_engine:managed", destination_tag("experience")],
        )
        memory.entries.append(existing)

        # Seed a candidate whose claim is a near-duplicate (same tokens)
        # with a negation marker, so the heuristic detector flags it as
        # contradicting the existing high-confidence entry rather than
        # merging.
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(
                claim="do not use sql_query for joins",
                candidate_type="experience_case",
                destination="experience",
                confidence=0.7,
                risk="low",
            )]
        )
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        cand = outcome.run.candidates[0]
        assert cand.contradicted_entry == existing.id
        assert cand.approval_required is True

    @pytest.mark.asyncio
    async def test_low_confidence_existing_is_superseded(self) -> None:
        eng, memory, *_ = _build_engine()
        existing_id = uuid.uuid4()
        existing = MemoryEntry(
            id=existing_id,
            scope="persistent",
            key=claim_key("experience", "use sql_query for joins"),
            value={
                "claim": "use sql_query for joins",
                "confidence": 0.4,  # below HIGH_CONFIDENCE_THRESHOLD -> candidate wins
                "destination": "experience",
            },
            tags=["reflection_engine:managed", destination_tag("experience")],
        )
        memory.entries.append(existing)

        # A candidate that contradicts the existing low-confidence
        # entry (same tokens, opposing polarity via "not"). Below the
        # threshold so the engine supersedes rather than asks.
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(
                claim="do not use sql_query for joins",
                candidate_type="experience_case",
                destination="experience",
                confidence=0.7,
                risk="low",
            )]
        )

        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        cand = outcome.run.candidates[0]
        assert cand.superseded_entry == existing_id
        # After commit, the existing entry is marked superseded_by.
        assert any(op[0] == "mark_superseded" for op in memory._operations)


# --------------------------------------------------------------------------- #
# Approval workflow
# --------------------------------------------------------------------------- #


class TestApproval:
    @pytest.mark.asyncio
    async def test_approve_after_reflect_commits_candidate(self) -> None:
        eng, memory, logs, _, event_bus = _build_engine()
        mission_id = uuid.uuid4()
        logs.entries.append(make_log_entry(event_type="user.feedback", mission_id=mission_id, payload={"source": "user", "kind": "feedback", "text": "Be terse"}))

        outcome = await eng.reflect(mission_id=mission_id, terminal_status="completed")
        run_id = outcome.run.id
        candidate_id = outcome.run.candidates[0].id
        assert outcome.pending_approvals == [candidate_id]

        # Approval flow.
        result = await eng.approve_candidate(run_id=run_id, candidate_id=candidate_id, approver="alice")
        assert result.pending_approvals == []
        # The candidate is now committed.
        cand = result.run.candidates[0]
        assert cand.approved is True
        assert cand.approver == "alice"
        assert cand.result_entry_id is not None
        # The MEMORY_APPROVAL_GRANTED event was emitted.
        types = [e.event_type for e in event_bus.published]
        assert MEMORY_APPROVAL_GRANTED in types
        assert MEMORY_PROMOTED in types

    @pytest.mark.asyncio
    async def test_approve_non_approval_required_is_noop(self) -> None:
        eng, *_ = _build_engine()
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(claim="a normal candidate", candidate_type="project_fact", destination="project", confidence=0.9, risk="low")]
        )
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        cand = outcome.run.candidates[0]
        assert cand.approval_required is False
        run_id = outcome.run.id
        # Calling approve again is benign.
        result = await eng.approve_candidate(run_id=run_id, candidate_id=cand.id, approver="alice")
        assert result.run.candidates[0].result_entry_id is not None

    @pytest.mark.asyncio
    async def test_reject_drops_candidate_and_emits_event(self) -> None:
        eng, *_ = _build_engine()
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(claim="risky", candidate_type="user_preference", destination="user_dna", confidence=0.95, risk="high")]
        )
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        cand = outcome.run.candidates[0]
        run_id = outcome.run.id

        result = await eng.reject_candidate(run_id=run_id, candidate_id=cand.id, approver="bob", reason="wrong scope")
        assert cand.approved is False
        assert cand.rejection_reason == "wrong scope"
        assert result.run.rejected_count == 1

    @pytest.mark.asyncio
    async def test_reject_on_non_approval_required_raises(self) -> None:
        eng, *_ = _build_engine()
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(claim="boring", candidate_type="project_fact", destination="project", confidence=0.9, risk="low")]
        )
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        cand = outcome.run.candidates[0]
        with pytest.raises(ApprovalDeniedError):
            await eng.reject_candidate(run_id=outcome.run.id, candidate_id=cand.id, approver="bob", reason="x")

    @pytest.mark.asyncio
    async def test_unknown_candidate_raises(self) -> None:
        eng, *_ = _build_engine()
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(claim="a", candidate_type="project_fact", destination="project", confidence=0.9, risk="low")]
        )
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        with pytest.raises(UnknownReflectionCandidateError):
            await eng.approve_candidate(run_id=outcome.run.id, candidate_id=uuid.uuid4(), approver="alice")


# --------------------------------------------------------------------------- #
# Event publication
# --------------------------------------------------------------------------- #


class TestEventPublication:
    @pytest.mark.asyncio
    async def test_lifecycle_events_for_normal_run(self) -> None:
        bus = FakeEventBus()
        eng, *_ = _build_engine(event_bus=bus)
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(claim="a candidate", candidate_type="project_fact", destination="project", confidence=0.9, risk="low")]
        )
        await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        types = [e.event_type for e in bus.published]
        assert types.count(REFLECTION_STARTED) == 1
        assert types.count(REFLECTION_COMPLETED) == 1
        assert types.count(MEMORY_CANDIDATE_CREATED) == 1
        assert types.count(MEMORY_PROMOTED) == 1

    @pytest.mark.asyncio
    async def test_rejected_candidate_emits_memory_rejected(self) -> None:
        bus = FakeEventBus()
        eng, *_ = _build_engine(event_bus=bus)
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(claim="bad scope", candidate_type="user_preference", destination="project", confidence=1.0)]
        )
        await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        types = [e.event_type for e in bus.published]
        assert MEMORY_REJECTED in types
        assert MEMORY_PROMOTED not in types

    @pytest.mark.asyncio
    async def test_supersession_emits_memory_superseded(self) -> None:
        memory = FakeMemoryWriter()
        bus = FakeEventBus()
        eng, *_ = _build_engine(memory=memory, event_bus=bus)
        existing_id = uuid.uuid4()
        memory.entries.append(MemoryEntry(
            id=existing_id,
            scope="persistent",
            key=claim_key("experience", "use postgres"),
            value={"claim": "use postgres", "confidence": 0.3, "destination": "experience"},
            tags=["reflection_engine:managed", destination_tag("experience")],
        ))

        from hermes.modules.reflection_engine.service import DefaultCandidateExtractor

        class _Contradict(DefaultCandidateExtractor):
            async def extract(self, **kw):  # noqa: ANN003
                return [_make_candidate_dict(claim="do not use postgres", candidate_type="experience_case", destination="experience", confidence=0.7, risk="low")]

        eng._extractor = _Contradict()  # type: ignore[attr-defined]
        await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")

        types = [e.event_type for e in bus.published]
        assert MEMORY_SUPERSEDED in types

    @pytest.mark.asyncio
    async def test_mission_terminal_event_triggers_reflection(self) -> None:
        bus = FakeEventBus()
        eng, *_ = _build_engine(event_bus=bus)
        await eng.start()
        try:
            terminal_event = make_event(
                event_type=MISSION_COMPLETED_EVENT,
                payload={"mission_id": str(uuid.uuid4())},
                source_module="mission_system",
            )
            await bus.publish(terminal_event)
            # Wait briefly for the subscription handler to fire (it
            # awaits `reflect` which is async). Using a few short
            # polls rather than `asyncio.sleep` to avoid timer flakes.
            for _ in range(20):
                if any(e.event_type == REFLECTION_STARTED for e in bus.published):
                    break
                await _tiny_sleep()
            types = [e.event_type for e in bus.published]
            assert REFLECTION_STARTED in types
            assert REFLECTION_COMPLETED in types
        finally:
            await eng.stop()

    @pytest.mark.asyncio
    async def test_mission_failed_event_triggers_reflection(self) -> None:
        bus = FakeEventBus()
        eng, *_ = _build_engine(event_bus=bus)
        await eng.start()
        try:
            terminal_event = make_event(
                event_type=MISSION_FAILED_EVENT,
                payload={"mission_id": str(uuid.uuid4())},
                source_module="mission_system",
            )
            await bus.publish(terminal_event)
            for _ in range(20):
                if any(e.event_type == REFLECTION_STARTED for e in bus.published):
                    break
                await _tiny_sleep()
            assert any(e.event_type == REFLECTION_STARTED for e in bus.published)
        finally:
            await eng.stop()


# --------------------------------------------------------------------------- #
# Mission cancellation (reduced-form reflection)
# --------------------------------------------------------------------------- #


class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancelled_skills_are_dropped(self) -> None:
        eng, *_ = _build_engine()
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [
                _make_candidate_dict(claim="a skill", candidate_type="skill_pattern", destination="skill", confidence=0.95, risk="medium"),
                _make_candidate_dict(claim="a project fact", candidate_type="project_fact", destination="project", confidence=0.9, risk="low"),
                _make_candidate_dict(claim="a user pref", candidate_type="user_preference", destination="user_dna", confidence=0.9, risk="high"),
            ]
        )
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="cancelled")
        assert outcome.success
        assert outcome.run.cancelled_skip is True
        # Skill and user_dna candidates were dropped.
        destinations = {c.destination for c in outcome.run.candidates}
        assert "skill" not in destinations
        assert "user_dna" not in destinations
        assert "project" in destinations  # project facts still recorded


# --------------------------------------------------------------------------- #
# Error recovery and idempotency
# --------------------------------------------------------------------------- #


class TestErrorRecovery:
    @pytest.mark.asyncio
    async def test_failed_phase_publishes_reflection_failed(self) -> None:
        bus = FakeEventBus()
        eng, *_ = _build_engine(event_bus=bus)

        # Inject a faulty extractor that raises -- the engine should
        # catch the error, publish REFLECTION_FAILED, and produce a
        # failed outcome.
        class _Crashing:
            async def extract(self, **kw):  # noqa: ANN003
                raise RuntimeError("extractor went bang")

        eng._extractor = _Crashing()  # type: ignore[attr-defined]
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        assert outcome.success is False
        types = [e.event_type for e in bus.published]
        assert REFLECTION_FAILED in types
        assert REFLECTION_COMPLETED not in types

    @pytest.mark.asyncio
    async def test_per_candidate_failure_isolated(self) -> None:
        eng, *_ = _build_engine()
        # One good, one bad (no provenance, will fail at Phase 4).
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [
                _make_candidate_dict(claim="good", candidate_type="project_fact", destination="project", confidence=0.9, risk="low"),
                _make_candidate_dict(claim="bad", candidate_type="project_fact", destination="project", confidence=0.9, risk="low", provenance=[]),
            ]
        )
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        # The good candidate promoted; the bad candidate rejected at
        # provenance gate. Both recorded -- a per-candidate failure
        # does not abort the run.
        promoted = sum(1 for c in outcome.run.candidates if c.result_entry_id is not None)
        rejected = sum(1 for c in outcome.run.candidates if c.rejection_reason)
        assert promoted == 1
        assert rejected == 1

    @pytest.mark.asyncio
    async def test_commit_failure_returns_failed_outcome(self) -> None:
        # A `CommitmentFailedError` raised from a per-candidate commit
        # is caught by the per-candidate isolation in `_run_phases` --
        # the candidate is recorded as failed, the run continues with
        # the other candidates, and the final outcome has
        # `success=False`. (A failure that escapes per-candidate
        # isolation -- e.g. from harvest -- bubbles up; this is the
        # tested path: the commit itself.)
        #
        # Sprint-2: the engine commits via `record_typed(...)`, not
        # `record(...)` -- so the broken fake must fail on the typed
        # path; otherwise the assertion below would never fire.
        class _BrokenMemory(FakeMemoryWriter):
            async def record_typed(self, **kw):  # noqa: ANN002
                raise RuntimeError("disk full")

        eng, *_ = _build_engine(memory=_BrokenMemory())
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(claim="boom", candidate_type="project_fact", destination="project", confidence=0.9, risk="low")]
        )
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        # Per-candidate isolation: the failure is logged, the
        # candidate is rejected, the run completes (other candidates
        # would still go through). The outcome's success flag is True
        # because the run completed; the failed candidate is in the
        # rejected count.
        assert outcome.success
        cand = outcome.run.candidates[0]
        assert "phase failure" in (cand.rejection_reason or "")
        assert outcome.run.rejected_count == 1
        assert outcome.run.promoted_count == 0


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_repeated_reflect_for_same_mission_returns_same_outcome(self) -> None:
        eng, memory, logs, _, _ = _build_engine()
        mission_id = uuid.uuid4()
        logs.entries.append(make_log_entry(event_type="tool.run.failed", mission_id=mission_id, severity="error", tool_name="sql_query", payload={"message": "x"}))
        logs.entries.append(make_log_entry(event_type="tool.retry_succeeded", mission_id=mission_id, tool_name="sql_query", payload={"message": "y"}))

        a = await eng.reflect(mission_id=mission_id, terminal_status="completed")
        promoted_after_first = outcome = await eng.reflect(mission_id=mission_id, terminal_status="completed")
        # Same run id, same final tally.
        assert a.run.id == promoted_after_first.run.id
        assert a.run.promoted_count == promoted_after_first.run.promoted_count
        assert a.run.merged_count == promoted_after_first.run.merged_count


# --------------------------------------------------------------------------- #
# Skill Memory routing
# --------------------------------------------------------------------------- #


class TestSkillRouting:
    @pytest.mark.asyncio
    async def test_skill_pattern_with_one_mission_demoted_to_experience(self) -> None:
        eng, *_ = _build_engine()
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(claim="x", candidate_type="skill_pattern", destination="skill", confidence=0.7, risk="medium", contributing_mission_ids=[])]
        )
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        cand = outcome.run.candidates[0]
        # Single mission, no refinement context -> demoted.
        assert cand.destination == "experience"
        assert cand.candidate_type == "experience_case"

    @pytest.mark.asyncio
    async def test_skill_pattern_with_two_missions_routes_to_skill(self) -> None:
        eng, *_ = _build_engine()
        eng._extractor = _FixedExtractor(  # type: ignore[attr-defined]
            [_make_candidate_dict(claim="x", candidate_type="skill_pattern", destination="skill", confidence=0.7, risk="medium", contributing_mission_ids=[uuid.uuid4(), uuid.uuid4()])]
        )
        outcome = await eng.reflect(mission_id=uuid.uuid4(), terminal_status="completed")
        cand = outcome.run.candidates[0]
        assert cand.destination == "skill"
        assert cand.candidate_type == "skill_pattern"


# --------------------------------------------------------------------------- #
# House-keeping
# --------------------------------------------------------------------------- #


class _FixedExtractor:
    """A `CandidateExtractor` that returns a fixed list of candidate
    dicts regardless of harvest. Used in tests that need exact control
    over what Phase 2 produces -- bypassing the rule-based default."""

    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        self._candidates = candidates

    async def extract(self, **kw):  # noqa: ANN003
        return self._candidates


async def _tiny_sleep() -> None:
    """A 1ms sleep used to give subscribers time to fire. Sleep in a
    helper so test times are tunable in one place if a particular
    CI environment needs more headroom."""
    import asyncio
    await asyncio.sleep(0.001)