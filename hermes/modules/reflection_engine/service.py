"""Reflection Engine -- the seven-phase reflection pipeline.

Implements `Specification/02 - Cognitive Architecture/Reflection Engine`
/ `ADR-0015` end-to-end. The seven phases are:

    1. Harvest           -- read Working Memory, Mission Memory, log
                            history, and the read-only context types
                            (User DNA / Skill / Experience / Project)
    2. Candidate Generation -- produce a candidate set, erring toward
                               over-candidacy
    3. Scoring & Routing -- confidence / scope_fit / risk; the Skill
                            Memory threshold (>=2 prior missions)
    4. Quality Gates     -- duplicate, contradiction, scope, provenance,
                            risk, threshold
    5. Human Approval    -- required for user_preference, contradictions,
                            high-risk, medium-risk below 0.7
    6. Commit            -- atomic write through Memory Manager
    7. Transition        -- publish `reflection.completed`, signal
                            Mission System via the event bus

Every write to a destination memory type goes through Memory Manager
(per `Reflection Engine`'s Design Decisions). The engine never writes
to memory directly.

Architectural decisions made and the conflicts they surface are
documented at the top of this module and in `README.md`. Two
outstanding architectural conflicts this implementation surfaces for
ADR review:

    C1. Memory Manager has no concept of the four destination memory
        types. Today this implementation encodes them via `scope` +
        `tags` (see `_DESTINATION_SCOPE`) -- a complete fit, but the
        type metadata lives in the entry's `value`, not in a first-class
        field. A future ADR may promote the four types to first-class
        fields on `MemoryEntry`.

    C2. Mission System publishes `mission_system.mission.completed` /
        `mission_system.mission.failed` but not a `cancelled` event.
        This engine listens to the two existing events; cancelled
        missions are not auto-reflected today. Surfacing as a separate
        ADR recommendation in the engineering report.

Both are documented in the engineering report as recommended ADRs; the
engine works end-to-end without either being resolved first.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.reflection_engine import events as evt
from hermes.modules.reflection_engine.contracts import (
    CandidateExtractor,
    LogQuerier,
    MemoryWriter,
    WorkingMemoryReader,
)
from hermes.modules.reflection_engine.errors import (
    ApprovalDeniedError,
    CandidateShapeError,
    CommitmentFailedError,
    ReflectionConfigError,
    UnknownReflectionCandidateError,
    UnknownReflectionRunError,
)
from hermes.modules.reflection_engine.models import (
    CandidateType,
    ConfidenceScore,
    DestinationType,
    GateVerdict,
    HIGH_CONFIDENCE_THRESHOLD,
    Provenance,
    ReflectionCandidate,
    ReflectionOutcome,
    ReflectionRun,
    ReflectionThresholds,
    RiskLevel,
    claim_key,
    destination_tag,
)

logger = logging.getLogger(__name__)

SOURCE_MODULE = "reflection_engine"

# Mission System's terminal events. Listed as constants here (rather
# than imported from mission_system/events.py) for two reasons:
#   1. The engine should not depend on Mission System's internals --
#      only on the event-bus contract.
#   2. The constant is what matters; the originating module's name is
#      already in the event_type string.
MISSION_COMPLETED_EVENT = "mission_system.mission.completed"
MISSION_FAILED_EVENT = "mission_system.mission.failed"

# The four destination memory types map onto Memory Manager's existing
# `scope` namespace plus a single `reflection:<destination>` tag at
# commit time. See C1 above.
_DESTINATION_SCOPE: dict[DestinationType, str] = {
    "user_dna": "persistent",
    "skill": "persistent",
    "experience": "persistent",
    "project": "persistent",
}

# Sprint-2: the engine writes into Memory Manager's first-class typed
# surface (`record_typed(...)`) so the canonical store for the four
# destinations is the `memory_type` field, not a tag encoding. The
# engine's vocabulary uses bare names (`user_dna`, `skill`,
# `experience`, `project`); Memory Galaxy's canonical names suffix
# `_memory` for the store categories (`skill_memory`,
# `experience_memory`, `project_memory`). This map translates
# engine vocabulary to the canonical MemoryType. The tag encoding
# below is preserved as a tag (not the canonical store) so legacy
# tag-filter readers keep working.
_DESTINATION_TO_MEMORY_TYPE: dict[DestinationType, str] = {
    "user_dna": "user_dna",
    "skill": "skill_memory",
    "experience": "experience_memory",
    "project": "project_memory",
}

# Tags every committed entry carries, so Phase-4 read-only-context
# queries can scope to one destination by tag.
_REFLECTION_TAG = "reflection_engine:managed"
_ORIGIN_TAG = "reflection:origin"
_SKILL_PATTERN_TAG = "reflection:skill_pattern"
_USER_DNA_TAG = "reflection:user_dna"

# Memory Manager keys are `claim_key(destination, claim)`. Keys for
# skill-pattern entries include the capability name so two skills that
# happen to share a claim string don't collide.
_SKILL_KEY_PREFIX = "skill:"


@dataclass
class _PendingRejection:
    """A rejection queued for publication. Held so the synchronous
    gate (`_reject`) does not call into the event loop directly. The
    loop flushes the queue at the end of a run so events arrive in
    the same order the gates ran them in."""

    run_id: uuid.UUID
    candidate_id: uuid.UUID
    gate: str
    reason: str
    mission_id: uuid.UUID


class ReflectionEngine:
    """The seven-phase pipeline. See module docstring for the
    architectural notes."""

    def __init__(
        self,
        *,
        memory: MemoryWriter,
        logs: LogQuerier | None = None,
        working_memory: WorkingMemoryReader | None = None,
        candidate_extractor: CandidateExtractor | None = None,
        event_bus: EventBus | None = None,
        thresholds: ReflectionThresholds | None = None,
        agent_id: str = "reflection_engine",
    ) -> None:
        if memory is None:
            raise ReflectionConfigError("ReflectionEngine requires a `memory` (MemoryWriter) collaborator")
        self._memory = memory
        self._logs = logs
        self._working_memory = working_memory
        self._extractor = candidate_extractor or DefaultCandidateExtractor()
        self._bus = event_bus
        self._thresholds = thresholds or ReflectionThresholds()
        self._agent_id = agent_id

        # One run per mission at a time. `reflect(...)` is idempotent
        # for un-committed work and atomic for committed work (per
        # `Reflection Engine` Phase 7) -- storing at most one in-flight
        # run per mission encodes that invariant at the engine layer.
        self._runs: dict[uuid.UUID, ReflectionRun] = {}
        self._outcomes: dict[uuid.UUID, ReflectionOutcome] = {}
        self._subscribed = False
        # Rejections queued during a synchronous gate pass and flushed
        # by `_flush_pending_rejections` at the end of the run.
        self._pending_rejections: list[_PendingRejection] = []

    # ====================================================================== #
    # Lifecycle
    # ====================================================================== #

    async def start(self) -> None:
        """Subscribes to mission-terminal events on the bus. A no-op
        if no bus was given, or already started."""
        if self._bus is not None and not self._subscribed:
            await self._bus.subscribe(MISSION_COMPLETED_EVENT, self._on_mission_terminal)
            await self._bus.subscribe(MISSION_FAILED_EVENT, self._on_mission_terminal)
            self._subscribed = True

    async def stop(self) -> None:
        if self._bus is not None and self._subscribed:
            await self._bus.unsubscribe(MISSION_COMPLETED_EVENT, self._on_mission_terminal)
            await self._bus.unsubscribe(MISSION_FAILED_EVENT, self._on_mission_terminal)
            self._subscribed = False

    # ====================================================================== #
    # Public API
    # ====================================================================== #

    async def reflect(
        self,
        *,
        mission_id: uuid.UUID,
        terminal_status: str,
    ) -> ReflectionOutcome:
        """Run the seven-phase pipeline for one mission. Idempotent
        for un-committed work; a finalised run for the same mission
        returns the recorded outcome."""
        if terminal_status not in ("completed", "failed", "cancelled"):
            raise CandidateShapeError(
                f"terminal_status must be 'completed', 'failed', or 'cancelled'; got {terminal_status!r}"
            )

        existing = self._in_flight_run(mission_id)
        if existing is not None and existing.is_finalised:
            # Idempotency: the previous run for this mission already
            # finalised. Returning the recorded outcome is the spec-
            # mandated behaviour ("reflection is idempotent for un-
            # committed work and atomic for committed work").
            return self._outcomes[existing.id]

        if existing is None:
            run = ReflectionRun(mission_id=mission_id, terminal_status=terminal_status)  # type: ignore[arg-type]
            self._runs[run.id] = run

        await self._publish(evt.REFLECTION_STARTED, run.id, {"mission_id": str(mission_id), "terminal_status": terminal_status})

        try:
            await self._run_phases(run)
        except Exception as exc:  # noqa: BLE001 -- top-of-pipeline safety net
            run.finalised_at = datetime.now(timezone.utc)
            outcome = ReflectionOutcome(run=run, success=False, failure_reason=str(exc))
            self._outcomes[run.id] = outcome
            await self._publish(
                evt.REFLECTION_FAILED,
                run.id,
                {"mission_id": str(mission_id), "error": str(exc), "phase": "pipeline"},
            )
            return outcome

        run.finalised_at = datetime.now(timezone.utc)
        pending = [c.id for c in run.candidates if c.approval_required and c.approved is None]
        outcome = ReflectionOutcome(run=run, success=True, pending_approvals=pending)
        self._outcomes[run.id] = outcome
        await self._publish(
            evt.REFLECTION_COMPLETED,
            run.id,
            {
                "mission_id": str(mission_id),
                "promoted": run.promoted_count,
                "rejected": run.rejected_count,
                "superseded": run.superseded_count,
                "merged": run.merged_count,
                "pending_approvals": len(pending),
            },
        )
        return outcome

    async def approve_candidate(
        self,
        *,
        run_id: uuid.UUID,
        candidate_id: uuid.UUID,
        approver: str,
    ) -> ReflectionOutcome:
        """Phase-5 approve. Records the approval, commits the candidate
        through Memory Manager, returns the updated outcome. Calling
        approve on an already-approved candidate is a no-op."""
        run = self._require_run(run_id)
        candidate = self._find_candidate(run, candidate_id)
        if not candidate.approval_required:
            # Approving a non-approval-required candidate is benign --
            # Phase 4 should have committed it directly. Treating this
            # as a no-op keeps the contract forgiving.
            return self._outcomes[run_id]
        if candidate.approved:
            return self._outcomes[run_id]

        candidate.approved = True
        candidate.approver = approver
        await self._publish(
            evt.MEMORY_APPROVAL_GRANTED,
            run.id,
            {"candidate_id": str(candidate.id), "approver": approver, "mission_id": str(run.mission_id)},
        )

        # Commit just this one candidate; the rest of the run is
        # already finalised or already in-flight.
        try:
            await self._commit_candidate(candidate)
        except Exception as exc:  # noqa: BLE001
            raise CommitmentFailedError(f"commit failed during approval: {exc}") from exc

        # Recompute the outcome's pending-approvals list.
        pending = [c.id for c in run.candidates if c.approval_required and c.approved is None]
        outcome = self._outcomes.get(run_id) or ReflectionOutcome(run=run, success=True)
        outcome.pending_approvals = pending
        return outcome

    async def reject_candidate(
        self,
        *,
        run_id: uuid.UUID,
        candidate_id: uuid.UUID,
        approver: str,
        reason: str,
    ) -> ReflectionOutcome:
        """Phase-5 reject. Records the rejection, drops the candidate,
        returns the updated outcome."""
        run = self._require_run(run_id)
        candidate = self._find_candidate(run, candidate_id)
        if not candidate.approval_required or candidate.approved is not None:
            # Rejecting a non-approval-required candidate is a
            # contract violation -- the candidate was either committed
            # directly or already resolved. Raise so callers can't
            # silently lose track of state.
            raise ApprovalDeniedError(
                f"candidate {candidate_id} is not currently awaiting approval "
                f"(approval_required={candidate.approval_required}, approved={candidate.approved})"
            )

        candidate.approved = False
        candidate.approver = approver
        candidate.rejection_reason = reason
        run.rejected_count += 1
        await self._publish(
            evt.MEMORY_APPROVAL_DENIED,
            run.id,
            {
                "candidate_id": str(candidate.id),
                "approver": approver,
                "reason": reason,
                "mission_id": str(run.mission_id),
            },
        )

        pending = [c.id for c in run.candidates if c.approval_required and c.approved is None]
        outcome = self._outcomes.get(run_id) or ReflectionOutcome(run=run, success=True)
        outcome.pending_approvals = pending
        return outcome

    def get_run(self, run_id: uuid.UUID) -> ReflectionRun:
        return self._require_run(run_id)

    def get_outcome(self, run_id: uuid.UUID) -> ReflectionOutcome:
        if run_id not in self._outcomes:
            raise UnknownReflectionRunError(self._runs[run_id].mission_id if run_id in self._runs else uuid.uuid4())
        return self._outcomes[run_id]

    def list_runs(self) -> list[ReflectionRun]:
        return list(self._runs.values())

    # ====================================================================== #
    # Event-bus handler
    # ====================================================================== #

    async def _on_mission_terminal(self, event: Event) -> None:
        """Mission System's terminal event handler. Triggers a
        reflection pass for the mission in the event payload. Any
        exception is logged but never propagated -- the event bus's
        `InMemoryEventBus._safe_invoke` swallows handler errors to
        protect other subscribers, but defending here as well means a
        networked bus (a future Redis/NATS adapter) won't surface our
        bugs as unhandled exceptions in the publisher."""
        try:
            mission_id_raw = event.payload.get("mission_id")
            if not mission_id_raw:
                logger.warning("reflection_engine: terminal event missing mission_id payload")
                return
            mission_id = uuid.UUID(str(mission_id_raw))
            terminal = "completed" if event.event_type == MISSION_COMPLETED_EVENT else "failed"
            await self.reflect(mission_id=mission_id, terminal_status=terminal)
        except Exception:  # noqa: BLE001
            logger.exception("reflection_engine: terminal-event handler crashed for %s", event.event_type)

    # ====================================================================== #
    # The seven phases
    # ====================================================================== #

    async def _run_phases(self, run: ReflectionRun) -> None:
        """Top-level phase runner. Cancelled missions get a reduced
        form per `Mission Lifecycle`'s "Memory Lifecycle" table: no
        Skill Memory or User DNA promotion (a cancelled mission is a
        user choice, not signal). Experience and Project Memory are
        still populated because they capture mission-scoped facts
        regardless of outcome."""

        # Phase 1: Harvest.
        harvested = await self._harvest(run.mission_id)
        read_only_context = await self._read_only_context()

        # Phase 2: Candidate Generation.
        raw_candidates = await self._extractor.extract(
            mission_id=run.mission_id,
            harvested=harvested,
            read_only_context=read_only_context,
        )

        for raw in raw_candidates:
            try:
                candidate = self._build_candidate(run.mission_id, raw)
            except CandidateShapeError as exc:
                logger.warning("reflection_engine: dropping malformed candidate: %s", exc)
                continue
            run.candidates.append(candidate)
            await self._publish(
                evt.MEMORY_CANDIDATE_CREATED,
                run.id,
                {
                    "candidate_id": str(candidate.id),
                    "destination": candidate.destination,
                    "type": candidate.candidate_type,
                    "mission_id": str(run.mission_id),
                },
            )

        # Cancelled missions: reduced-form reflection. Drop
        # skill-pattern and user_dna candidates here; route the rest
        # to the normal Phase-3-7 flow.
        if run.terminal_status == "cancelled":
            run.cancelled_skip = True
            run.candidates = [c for c in run.candidates if c.destination not in ("skill", "user_dna")]
            for c in run.candidates:
                c.rejection_reason = c.rejection_reason or "cancelled missions follow reduced-form reflection"

        # Phases 3-6: score, route, gate, commit. Each candidate is
        # processed independently -- a failure on one candidate does
        # not abort the others (per `Memory Galaxy`'s additive-only
        # stance: partial progress is preferable to losing the run).
        for candidate in run.candidates:
            try:
                await self._score_and_route(run, candidate)
                passed_gates = await self._apply_quality_gates(run, candidate)
                if passed_gates and not candidate.approval_required:
                    await self._commit_candidate(candidate)
                elif passed_gates and candidate.approval_required:
                    # Approval-required candidates stay unactioned
                    # until `approve_candidate`/`reject_candidate` is
                    # called. They are tracked in
                    # `outcome.pending_approvals` so the Mission System
                    # can hold the mission terminal until resolution.
                    pass
                else:
                    # Dropped at a gate -- count it.
                    if candidate.rejection_reason:
                        run.rejected_count += 1
            except Exception as exc:  # noqa: BLE001 -- per-candidate isolation
                logger.exception("reflection_engine: candidate %s failed during phases 3-6", candidate.id)
                candidate.rejection_reason = f"phase failure: {exc}"
                run.rejected_count += 1

        # Drain rejection events before the completion event so
        # subscribers see them in deterministic gate order.
        await self._flush_pending_rejections(run)

        # Phase 7 is signalled from `reflect(...)` itself -- the
        # transition event is published there so the outcome and the
        # event carry identical data.

    # ----- Phase 1: Harvest ----------------------------------------------- #

    async def _harvest(self, mission_id: uuid.UUID) -> list[Any]:
        """Collect the mission's Working Memory, the log history, and
        any mission-scoped Memory Manager entries. Each harvest source
        is independently fault-tolerant: a failed source is logged and
        skipped rather than aborting the whole harvest -- a mission
        with no logs still reflects."""
        harvested: list[Any] = []

        if self._working_memory is not None:
            try:
                working = await self._working_memory.query(
                    requesting_agent_id=self._agent_id, session_id=str(mission_id), scope="session"
                )
                harvested.extend(working)
            except Exception:  # noqa: BLE001
                logger.exception("reflection_engine: working-memory harvest failed")

        if self._logs is not None:
            try:
                logs = await self._logs.query(mission_id=mission_id)
                harvested.extend(logs)
            except Exception:  # noqa: BLE001
                logger.exception("reflection_engine: log harvest failed")

        try:
            mission_entries = await self._memory.query(
                requesting_agent_id=self._agent_id, scope="workflow", session_id=str(mission_id)
            )
            harvested.extend(mission_entries)
        except Exception:  # noqa: BLE001
            logger.exception("reflection_engine: mission-memory harvest failed")

        return harvested

    async def _read_only_context(self) -> list[Any]:
        """Fetch every existing reflection-managed entry from the
        four destinations, for duplicate / contradiction detection in
        Phase 4. Read-only by construction: Phase 6's writes go
        through `_commit_candidate`, never by mutating this list."""
        entries: list[Any] = []
        for destination in ("user_dna", "skill", "experience", "project"):
            try:
                rows = await self._memory.query(
                    requesting_agent_id=self._agent_id, scope=_DESTINATION_SCOPE[destination], tags=[_REFLECTION_TAG, destination_tag(destination)]
                )
                entries.extend(rows)
            except Exception:  # noqa: BLE001
                logger.exception("reflection_engine: read-only-context fetch failed for %s", destination)
        return entries

    # ----- Phase 2 -> 3: candidate shaping + scoring --------------------- #

    def _build_candidate(self, mission_id: uuid.UUID, raw: dict[str, Any]) -> ReflectionCandidate:
        """Validate a raw extractor output into a typed
        `ReflectionCandidate`. The extractor is a Protocol that may
        be backed by anything from rule-based heuristics to an LLM --
        validation here is the engine's safety net for bad extractor
        output."""
        if not isinstance(raw, dict):
            raise CandidateShapeError(f"extractor returned non-dict: {type(raw).__name__}")
        claim = raw.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            raise CandidateShapeError("extractor candidate missing non-empty 'claim'")
        candidate_type = raw.get("candidate_type")
        if candidate_type not in ("user_preference", "project_fact", "skill_pattern", "experience_case"):
            raise CandidateShapeError(f"extractor candidate type {candidate_type!r} not in the four scope-proposal types")
        destination = raw.get("destination")
        if destination not in ("user_dna", "skill", "experience", "project"):
            raise CandidateShapeError(f"extractor destination {destination!r} not in the four destinations")
        score_raw = raw.get("score") or {}
        try:
            score = ConfidenceScore(
                confidence=float(score_raw.get("confidence", 0.0)),
                scope_fit=float(score_raw.get("scope_fit", 0.0)),
                risk=score_raw.get("risk", "low"),
            )
        except Exception as exc:
            raise CandidateShapeError(f"extractor score invalid: {exc}") from exc

        provenance = []
        for p in raw.get("provenance", []) or []:
            if not isinstance(p, dict):
                continue
            try:
                provenance.append(
                    Provenance(
                        source_type=p.get("source_type", "synthetic"),
                        source_id=str(p.get("source_id", "")),
                        description=str(p.get("description", "")),
                        weight=float(p.get("weight", 1.0)),
                    )
                )
            except Exception:  # noqa: BLE001
                continue

        contributing = [uuid.UUID(str(m)) for m in raw.get("contributing_mission_ids", []) if m]

        return ReflectionCandidate(
            mission_id=mission_id,
            claim=claim.strip(),
            candidate_type=candidate_type,  # type: ignore[arg-type]
            destination=destination,  # type: ignore[arg-type]
            provenance=provenance,
            score=score,
            contributing_mission_ids=contributing,
        )

    # ----- Phase 3: Scoring & Routing ------------------------------------ #

    async def _score_and_route(self, run: ReflectionRun, candidate: ReflectionCandidate) -> None:
        """Phase 3 + the Skill Memory threshold gate.

        The Skill Memory threshold (>=2 prior missions, or one prior
        mission with confidence >=0.9 and refinement context) is
        enforced here, NOT in Phase 4's quality gates -- because the
        threshold changes the candidate's destination, not its
        fate as 'accepted/rejected.' A pattern seen once becomes an
        experience_case (Phase 3 routing decision) rather than being
        rejected by Phase 4.
        """
        if candidate.destination == "skill" and candidate.candidate_type == "skill_pattern":
            if not self._routes_to_skill(run, candidate):
                candidate.destination = "experience"
                candidate.candidate_type = "experience_case"

    def _routes_to_skill(self, run: ReflectionRun, candidate: ReflectionCandidate) -> bool:
        """True iff the candidate qualifies for Skill Memory under
        the spec's threshold rule."""
        contributing = len(candidate.contributing_mission_ids)
        if contributing >= self._thresholds.skill_min_missions:
            return True
        # Single-mission refinement exception: one prior mission with
        # confidence >= 0.9 and the candidate references an existing
        # skill entry (signalled by `contributing_mission_ids` having
        # exactly one entry). Without refinement context, single-
        # occurrence patterns are demoted.
        if contributing == 1 and candidate.score.confidence >= self._thresholds.skill_single_mission_refinement_min:
            return True
        return False

    # ----- Phase 4: Quality Gates ---------------------------------------- #

    async def _apply_quality_gates(self, run: ReflectionRun, candidate: ReflectionCandidate) -> bool:
        """Returns True iff the candidate should proceed to commit
        (with or without Phase-5 approval). Returning False means
        the candidate was dropped at one of the gates.

        Gate order matches `Reflection Engine` Phase 4:
            1. provenance gate
            2. scope gate
            3. duplicate detection
            4. contradiction detection
            5. threshold gate
            6. risk gate (sets `approval_required` but does not drop)
        """
        # 1. Provenance -- a candidate with no evidence is dropped.
        if not candidate.provenance:
            return self._reject(run, candidate, "provenance", "no provenance", "drop")
        run.verdicts.append(GateVerdict(gate="provenance", outcome="pass", action="accept"))

        # 2. Scope gate -- the candidate_type must match the destination.
        if not self._scope_matches(candidate):
            return self._reject(run, candidate, "scope", "scope does not match destination", "drop")
        run.verdicts.append(GateVerdict(gate="scope", outcome="pass", action="accept"))

        # 3. Duplicate detection -- read-only-context query by key.
        existing = await self._find_existing_entry(candidate)
        if existing is not None:
            return await self._handle_duplicate(run, candidate, existing)

        # 4. Contradiction detection -- same query, but the existing
        #    entry's value conflicts with the candidate's claim. We
        #    use a textual heuristic (the existing entry's claim
        #    contains a negation / opposite marker) since semantic
        #    similarity is a Future Consideration per `Reflection
        #    Engine`. A future embedding-backed detector can replace
        #    this without changing the gate's contract.
        contradicting = await self._find_contradicting_entry(candidate)
        if contradicting is not None:
            return await self._handle_contradiction(run, candidate, contradicting)

        # 5. Threshold gate.
        floor = self._threshold_for(candidate.destination)
        if candidate.score.confidence < floor:
            return self._reject(
                run,
                candidate,
                "threshold",
                f"confidence {candidate.score.confidence:.2f} below {candidate.destination} floor {floor:.2f}",
                "drop",
            )
        run.verdicts.append(GateVerdict(gate="threshold", outcome="pass", action="accept"))

        # 6. Risk gate -- sets approval_required but does not drop.
        if self._requires_approval(candidate):
            candidate.approval_required = True
            run.verdicts.append(GateVerdict(gate="approval", outcome="needs_approval", action="flag", reason="risk gate"))
        return True

    def _scope_matches(self, candidate: ReflectionCandidate) -> bool:
        """The candidate_type's scope proposal must match the
        destination. A user_preference must route to user_dna; a
        skill_pattern to skill; a project_fact to project (or, if no
        project is set, demoted to experience per Phase 3 routing --
        that demotion happened upstream in `_score_and_route`); an
        experience_case to experience."""
        return {
            "user_preference": "user_dna",
            "skill_pattern": "skill",
            "project_fact": "project",
            "experience_case": "experience",
        }.get(candidate.candidate_type) == candidate.destination

    async def _find_existing_entry(self, candidate: ReflectionCandidate) -> Any | None:
        """Lookup by `(destination, claim_key)` -- returns the existing
        entry if one is already managed by the engine at this
        destination, else None. Idempotency for re-running reflection
        on the same mission: a candidate whose claim already exists
        is merged, not duplicated."""
        try:
            entries = await self._memory.query(
                requesting_agent_id=self._agent_id,
                scope=_DESTINATION_SCOPE[candidate.destination],
                tags=[_REFLECTION_TAG, destination_tag(candidate.destination)],
            )
        except Exception:  # noqa: BLE001
            return None
        key = claim_key(candidate.destination, candidate.claim)
        for entry in entries:
            if getattr(entry, "key", None) == key:
                return entry
        return None

    async def _find_contradicting_entry(self, candidate: ReflectionCandidate) -> Any | None:
        """Heuristic contradiction detection. Today's Memory Manager
        has no embedding search (per the `Memory Galaxy` Future
        Considerations) -- so we look for an existing entry at the
        same destination whose claim's tokens are mostly the same but
        whose claim contains an explicit negation marker.

        Markers: "not", "never", "no longer", "don't", "stop",
        "without". A future embedding-backed detector can replace
        this without changing the gate's contract; the engine treats
        the contradiction case identically either way (it routes to
        Phase-5 approval / Phase-6 supersession).
        """
        try:
            entries = await self._memory.query(
                requesting_agent_id=self._agent_id,
                scope=_DESTINATION_SCOPE[candidate.destination],
                tags=[_REFLECTION_TAG, destination_tag(candidate.destination)],
            )
        except Exception:  # noqa: BLE001
            return None
        cand_tokens = set(candidate.claim.lower().split())
        negation_markers = {"not", "never", "no", "don't", "stop", "without", "no longer"}
        for entry in entries:
            other_claim = (entry.value or {}).get("claim") if hasattr(entry, "value") else None
            if not isinstance(other_claim, str):
                continue
            other_tokens = set(other_claim.lower().split())
            if not cand_tokens or not other_tokens:
                continue
            # Same destination, substantial overlap, opposing polarity.
            overlap = cand_tokens & other_tokens
            if len(overlap) < max(2, len(cand_tokens) // 2):
                continue
            if (cand_tokens & negation_markers) ^ (other_tokens & negation_markers):
                return entry
        return None

    async def _handle_duplicate(self, run: ReflectionRun, candidate: ReflectionCandidate, existing: Any) -> bool:
        """Phase-4 duplicate sub-case. Per `Reflection Engine`:
        near-duplicates merge with confidence updated to max; the
        merged provenance is appended. The merge is idempotent: a
        second merge with the same candidate is a no-op."""
        existing_id = getattr(existing, "id", None)
        if existing_id is None:
            return self._reject(run, candidate, "duplicate", "existing entry has no id", "drop")
        candidate.merged_into = existing_id
        candidate.rejection_reason = "merged into existing entry"
        existing_conf = (existing.value or {}).get("confidence", 0.0) if hasattr(existing, "value") else 0.0
        new_conf = max(float(existing_conf), candidate.score.confidence)
        # Re-write the existing entry with merged value. The Memory
        # Manager's `save()` upserts by `(scope, owner, key)`, so the
        # existing entry is updated in place rather than duplicated.
        merged_value = dict(existing.value or {})
        merged_value["confidence"] = new_conf
        merged_provenance = list(merged_value.get("provenance", []))
        merged_provenance.extend(
            {
                "source_type": p.source_type,
                "source_id": p.source_id,
                "description": p.description,
                "weight": p.weight,
            }
            for p in candidate.provenance
        )
        merged_value["provenance"] = merged_provenance
        merged_value["merged_from_mission_ids"] = sorted(
            set(merged_value.get("merged_from_mission_ids", []) + [str(candidate.mission_id)])
        )
        try:
            await self._memory.record(
                requesting_agent_id=self._agent_id,
                scope=_DESTINATION_SCOPE[candidate.destination],
                key=getattr(existing, "key", None) or claim_key(candidate.destination, candidate.claim),
                value=merged_value,
                owner_agent_id=getattr(existing, "owner_agent_id", None),
                tags=list(getattr(existing, "tags", []) or []),
                backlinks=list(getattr(existing, "backlinks", []) or []),
            )
        except Exception as exc:  # noqa: BLE001
            return self._reject(run, candidate, "duplicate", f"merge failed: {exc}", "drop")
        run.merged_count += 1
        candidate.result_entry_id = existing_id
        run.verdicts.append(
            GateVerdict(gate="duplicate", outcome="pass", action="merge", target_entry_id=existing_id)
        )
        await self._publish(
            evt.MEMORY_PROMOTED,
            run.id,
            {
                "candidate_id": str(candidate.id),
                "destination": candidate.destination,
                "action": "merged",
                "target_entry_id": str(existing_id),
                "mission_id": str(run.mission_id),
            },
        )
        return True  # merged -- treat as committed for outcome purposes

    async def _handle_contradiction(
        self, run: ReflectionRun, candidate: ReflectionCandidate, existing: Any
    ) -> bool:
        """Phase-4 contradiction sub-case. Per `Reflection Engine`:
        - high-confidence existing (>=0.9) -> human approval (both
          candidate and existing presented); old entry is NOT
          auto-superseded
        - lower-confidence existing -> candidate wins; old is
          marked superseded_by (additive only -- never deleted)
        - close-confidence same-run candidates -> both flagged for
          approval. Same-run contradiction between two NEW candidates
          is detected and handled separately (see
          `_close_confidence_contradictions`).
        """
        existing_id = getattr(existing, "id", None)
        existing_conf = float((existing.value or {}).get("confidence", 0.0)) if hasattr(existing, "value") else 0.0
        candidate.contradicted_entry = existing_id

        if existing_conf >= HIGH_CONFIDENCE_THRESHOLD:
            # Per spec: high-confidence existing triggers human
            # approval of both the candidate and the existing entry.
            candidate.approval_required = True
            candidate.approval_reason = "contradicts high-confidence existing entry"
            run.verdicts.append(
                GateVerdict(
                    gate="contradiction",
                    outcome="needs_approval",
                    action="flag",
                    target_entry_id=existing_id,
                    reason="high-confidence existing",
                )
            )
            return True

        # Lower-confidence existing: candidate wins. Old entry is
        # marked superseded_by, not deleted.
        run.verdicts.append(
            GateVerdict(
                gate="contradiction",
                outcome="pass",
                action="supersede",
                target_entry_id=existing_id,
                reason=f"existing confidence {existing_conf:.2f} < {HIGH_CONFIDENCE_THRESHOLD}",
            )
        )
        # Defer the actual mark-superseded until Phase 6 succeeds --
        # if the new commit fails, the old entry remains the active
        # one. `_commit_candidate` does the mark.
        candidate.superseded_entry = existing_id
        return True

    def _requires_approval(self, candidate: ReflectionCandidate) -> bool:
        """Approval rules per `Reflection Engine` Phase 5:
            - every user_preference (User DNA writes are high-risk)
            - every contradiction case (already set upstream)
            - every high-risk candidate of any type
            - every medium-risk candidate with confidence < 0.7
            - Skill Memory patterns (the threshold gate is a separate
              check; the approval gate ALSO requires human approval
              for skill promotions because they generalise across
              missions)
        """
        if candidate.candidate_type == "user_preference":
            return True
        if candidate.candidate_type == "skill_pattern" or candidate.destination == "skill":
            return True
        if candidate.score.risk == "high":
            return True
        if candidate.score.risk == "medium" and candidate.score.confidence < self._thresholds.medium_risk_approval_floor:
            return True
        return False

    def _threshold_for(self, destination: DestinationType) -> float:
        return {
            "user_dna": self._thresholds.user_dna_min,
            "project": self._thresholds.project_min,
            "skill": self._thresholds.skill_min,
            "experience": self._thresholds.experience_min,
        }[destination]

    def _reject(
        self,
        run: ReflectionRun,
        candidate: ReflectionCandidate,
        gate: str,
        reason: str,
        action: str,
    ) -> bool:
        candidate.rejection_reason = reason
        run.verdicts.append(GateVerdict(gate=gate, outcome="fail", action=action, reason=reason))
        # Queue the rejection event for flush at the end of the run.
        # The queue is bounded by the number of candidates in one
        # reflection pass -- bounded enough that no eviction is needed.
        self._pending_rejections.append(_PendingRejection(run.id, candidate.id, gate, reason, run.mission_id))
        return False

    async def _flush_pending_rejections(self, run: ReflectionRun) -> None:
        """Publish all queued MEMORY_REJECTED events for one run, in
        gate-decision order. Called from `_run_phases` so events
        arrive before `REFLECTION_COMPLETED` -- subscribers can
        therefore rely on event ordering: every rejection for a run
        precedes its completion event."""
        if not self._pending_rejections:
            return
        pending = [p for p in self._pending_rejections if p.run_id == run.id]
        for p in pending:
            await self._publish(
                evt.MEMORY_REJECTED,
                p.run_id,
                {
                    "candidate_id": str(p.candidate_id),
                    "gate": p.gate,
                    "reason": p.reason,
                    "mission_id": str(p.mission_id),
                },
            )
        for p in pending:
            try:
                self._pending_rejections.remove(p)
            except ValueError:
                pass

    # ----- Phase 6: Commit ----------------------------------------------- #

    async def _commit_candidate(self, candidate: ReflectionCandidate) -> None:
        """Write one candidate to its destination via Memory Manager's
        typed surface (`record_typed(...)`). Sprint-1 wrote to a
        `scope="persistent"` + tags encoding (`C1` shim); Sprint-2
        writes to the first-class `memory_type` field so the canonical
        store is no longer the tag encoding. The destination tag is
        still added (so Phase-4 read-only-context queries that haven't
        migrated to `query(memory_type=...)` keep working until they
        migrate too).

        Marks superseded entries (Phase 4's contradiction sub-case)
        AFTER the new write succeeds -- if the write fails, the old
        entry remains the active one, preserving `Memory Galaxy`'s
        additive-only rule under failure.
        """
        # Sprint-2 typed write: the canonical store for the
        # candidate's data is now the typed fields
        # (`memory_type`, `confidence`, `provenance`,
        # `relationships`). The `value` dict now carries only
        # the engine-specific extras that don't have first-class
        # fields (claim text, candidate type, scope fit, risk,
        # contributing mission ids). Confidence is duplicated in
        # the typed `confidence` field AND inside `value` only
        # for backward compatibility of any consumer that read
        # `value['confidence']` before the migration.
        value = {
            "claim": candidate.claim,
            "destination": candidate.destination,
            "candidate_type": candidate.candidate_type,
            "scope_fit": candidate.score.scope_fit,
            "risk": candidate.score.risk,
            "source_mission_id": str(candidate.mission_id),
            "contributing_mission_ids": [str(m) for m in candidate.contributing_mission_ids],
            "confidence": candidate.score.confidence,
        }
        if candidate.approved and candidate.approver:
            value["approver"] = candidate.approver

        # Tags: every committed entry carries the engine's managed
        # marker plus the destination tag, so Phase 4's read-only-
        # context queries can scope by tag without scanning every
        # `scope=persistent` entry. The destination tag is now
        # metadata, not the canonical store.
        tags = [_REFLECTION_TAG, destination_tag(candidate.destination)]
        if candidate.destination == "user_dna":
            tags.append(_USER_DNA_TAG)
        if candidate.candidate_type == "skill_pattern":
            tags.append(_SKILL_PATTERN_TAG)
        tags.append(f"{_ORIGIN_TAG}:{candidate.mission_id}")

        key = self._commit_key(candidate)
        # Sprint-2: convert engine-local `Provenance` instances into
        # the dict shape Memory Manager expects, so the typed-write
        # validation can build its own `Provenance` from the dicts.
        # (The engine and Memory Manager each define their own
        # `Provenance` Pydantic class -- structurally identical but
        # not the same class object.)
        provenance_dicts = [
            {
                "source_type": p.source_type,
                "source_id": p.source_id,
                "description": p.description,
                "weight": p.weight,
            }
            for p in candidate.provenance
        ]
        try:
            entry = await self._memory.record_typed(
                requesting_agent_id=self._agent_id,
                memory_type=_DESTINATION_TO_MEMORY_TYPE[candidate.destination],
                key=key,
                value=value,
                scope=_DESTINATION_SCOPE[candidate.destination],
                confidence=candidate.score.confidence,
                provenance=provenance_dicts,
                tags=tags,
                origin_mission_id=candidate.mission_id,
            )
        except Exception as exc:
            raise CommitmentFailedError(f"memory.record_typed failed for candidate {candidate.id}: {exc}") from exc

        candidate.result_entry_id = getattr(entry, "id", None)
        run = self._run_for_candidate(candidate)
        if run is not None:
            run.promoted_count += 1
        await self._publish(
            evt.MEMORY_PROMOTED,
            run.id if run else uuid.uuid4(),
            {
                "candidate_id": str(candidate.id),
                "destination": candidate.destination,
                "entry_id": str(candidate.result_entry_id) if candidate.result_entry_id else None,
                "mission_id": str(candidate.mission_id),
            },
        )

        # Post-commit supersession: the old entry is now safely
        # marked superseded_by the new entry.
        if candidate.superseded_entry is not None and candidate.result_entry_id is not None:
            try:
                await self._memory.mark_superseded(
                    requesting_agent_id=self._agent_id,
                    entry_id=candidate.superseded_entry,
                    superseded_by=candidate.result_entry_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception("reflection_engine: mark_superseded failed for %s", candidate.superseded_entry)
            else:
                if run is not None:
                    run.superseded_count += 1
                await self._publish(
                    evt.MEMORY_SUPERSEDED,
                    run.id if run else uuid.uuid4(),
                    {
                        "candidate_id": str(candidate.id),
                        "destination": candidate.destination,
                        "superseded_entry_id": str(candidate.superseded_entry),
                        "superseded_by_entry_id": str(candidate.result_entry_id),
                        "mission_id": str(candidate.mission_id),
                    },
                )

    def _commit_key(self, candidate: ReflectionCandidate) -> str:
        """The Memory Manager key under which the entry is stored.

        Skill patterns include the contributing missions' hash so two
        skill patterns that share a claim string but different
        evidence don't collide on the key index. Other destinations
        use the (destination, normalised claim) key.
        """
        if candidate.destination == "skill":
            contrib = sorted(str(m) for m in candidate.contributing_mission_ids) or [str(candidate.mission_id)]
            return _SKILL_KEY_PREFIX + claim_key("skill", candidate.claim) + ":" + "|".join(contrib)
        return claim_key(candidate.destination, candidate.claim)

    # ====================================================================== #
    # Internals
    # ====================================================================== #

    def _in_flight_run(self, mission_id: uuid.UUID) -> ReflectionRun | None:
        """Returns the active run for a mission, if any -- either
        in-progress or finalised. Used by `reflect(...)`'s idempotency
        check."""
        for run in self._runs.values():
            if run.mission_id == mission_id:
                return run
        return None

    def _require_run(self, run_id: uuid.UUID) -> ReflectionRun:
        if run_id not in self._runs:
            raise UnknownReflectionRunError(run_id)
        return self._runs[run_id]

    def _find_candidate(self, run: ReflectionRun, candidate_id: uuid.UUID) -> ReflectionCandidate:
        for candidate in run.candidates:
            if candidate.id == candidate_id:
                return candidate
        raise UnknownReflectionCandidateError(candidate_id)

    def _run_for_candidate(self, candidate: ReflectionCandidate) -> ReflectionRun | None:
        for run in self._runs.values():
            if any(c.id == candidate.id for c in run.candidates):
                return run
        return None

    async def _publish(self, event_type: str, run_id: uuid.UUID, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module=SOURCE_MODULE,
                correlation_id=run_id,
                payload={"run_id": str(run_id), **payload},
            )
        )


# ---------------------------------------------------------------------------
# Default candidate extractor
# ---------------------------------------------------------------------------


class DefaultCandidateExtractor:
    """A rule-based extractor that turns harvested signals into
    candidate lessons.

    Today's rule set:

    - A log entry tagged `error` (severity=="error") with a sibling
      log entry tagged `info` carrying a recovery action within the
      same mission generates one `experience_case` candidate
      describing the recovery.
    - A user-feedback log entry (event_type contains "user.feedback"
      or "user.correction") generates one `user_preference` candidate.
    - A repeated `tool.*.invocation` log entry (same tool, same
      mission, >=3 occurrences) generates one `skill_pattern`
      candidate naming the tool.
    - A `mission.*` event whose payload declares a project id
      (project_id key present) plus a `decision` log entry generates
      one `project_fact` candidate summarising the decision.

    This is intentionally minimal -- the directive's "no LLM calls"
    posture and the absence of a real model means the extractor is a
    deterministic seed, not the long-term design. A future LLM-backed
    extractor implementing the `CandidateExtractor` Protocol can
    replace this without engine changes.
    """

    async def extract(
        self,
        *,
        mission_id: uuid.UUID,
        harvested: list[Any],
        read_only_context: list[Any],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        errors = [e for e in harvested if _severity(e) == "error"]
        recoveries = [e for e in harvested if _is_recovery(e)]

        for rec in recoveries:
            related_errors = [e for e in errors if _same_source(rec, e)]
            if not related_errors:
                continue
            tool = _tool_name(rec)
            out.append(
                _make_candidate(
                    mission_id=mission_id,
                    claim=f"Recovered from {_first_error_summary(related_errors)} via {tool or 'recovery action'}",
                    candidate_type="experience_case",
                    destination="experience",
                    confidence=0.6,
                    risk="low",
                    provenance=_provenance_from(related_errors + [rec]),
                )
            )

        feedback = [e for e in harvested if _is_user_feedback(e)]
        for fbk in feedback:
            out.append(
                _make_candidate(
                    mission_id=mission_id,
                    claim=_feedback_claim(fbk),
                    candidate_type="user_preference",
                    destination="user_dna",
                    confidence=0.7,
                    risk="high",
                    provenance=_provenance_from([fbk]),
                )
            )

        tool_counts: dict[str, int] = {}
        tool_examples: dict[str, list[Any]] = {}
        for e in harvested:
            t = _tool_name(e)
            if not t:
                continue
            tool_counts[t] = tool_counts.get(t, 0) + 1
            tool_examples.setdefault(t, []).append(e)

        for t, count in tool_counts.items():
            if count >= 3:
                out.append(
                    _make_candidate(
                        mission_id=mission_id,
                        claim=f"Tool {t} is repeatedly useful on this mission shape",
                        candidate_type="skill_pattern",
                        destination="skill",
                        confidence=min(0.95, 0.5 + 0.1 * count),
                        risk="medium",
                        provenance=_provenance_from(tool_examples[t][:3]),
                        contributing_mission_ids=[mission_id],
                    )
                )

        decisions = [e for e in harvested if _is_decision(e)]
        projects = {e.payload.get("project_id") for e in harvested if isinstance(getattr(e, "payload", None), dict) and e.payload.get("project_id")}
        if projects and decisions:
            out.append(
                _make_candidate(
                    mission_id=mission_id,
                    claim=f"Project decisions made: {'; '.join(_decision_summary(d) for d in decisions[:3])}",
                    candidate_type="project_fact",
                    destination="project",
                    confidence=0.65,
                    risk="medium",
                    provenance=_provenance_from(decisions[:3]),
                )
            )

        return out


def _make_candidate(
    *,
    mission_id: uuid.UUID,
    claim: str,
    candidate_type: CandidateType,
    destination: DestinationType,
    confidence: float,
    risk: RiskLevel,
    provenance: list[Provenance],
    contributing_mission_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    return {
        "claim": claim,
        "candidate_type": candidate_type,
        "destination": destination,
        "score": {"confidence": confidence, "scope_fit": 0.8, "risk": risk},
        "provenance": [p.model_dump() for p in provenance],
        "contributing_mission_ids": [str(m) for m in (contributing_mission_ids or [])],
    }


def _severity(entry: Any) -> str | None:
    if hasattr(entry, "severity"):
        return getattr(entry, "severity", None)
    return None


def _tool_name(entry: Any) -> str | None:
    if hasattr(entry, "tool_name"):
        return getattr(entry, "tool_name", None)
    if isinstance(getattr(entry, "payload", None), dict):
        return entry.payload.get("tool_name")
    return None


def _payload(entry: Any) -> dict[str, Any]:
    if isinstance(getattr(entry, "payload", None), dict):
        return entry.payload
    return {}


def _event_type(entry: Any) -> str:
    return getattr(entry, "event_type", "") or ""


def _is_recovery(entry: Any) -> bool:
    et = _event_type(entry).lower()
    return "recover" in et or "retry_succeeded" in et or "fixed" in et


def _is_user_feedback(entry: Any) -> bool:
    et = _event_type(entry).lower()
    if "user.feedback" in et or "user.correction" in et or "user.confirmation" in et:
        return True
    p = _payload(entry)
    if p.get("source") == "user" and p.get("kind") in {"feedback", "correction", "confirmation"}:
        return True
    return False


def _is_decision(entry: Any) -> bool:
    et = _event_type(entry).lower()
    return "decision" in et


def _same_source(a: Any, b: Any) -> bool:
    return _tool_name(a) == _tool_name(b) and bool(_tool_name(a))


def _first_error_summary(errors: list[Any]) -> str:
    if not errors:
        return "an error"
    e = errors[0]
    p = _payload(e)
    msg = p.get("message") or p.get("error") or _event_type(e) or "error"
    return str(msg)[:80]


def _feedback_claim(feedback: Any) -> str:
    p = _payload(feedback)
    if p.get("text"):
        return str(p["text"])[:200]
    return f"User feedback recorded: {_event_type(feedback)}"


def _decision_summary(decision: Any) -> str:
    p = _payload(decision)
    return str(p.get("summary") or p.get("description") or _event_type(decision))[:120]


def _provenance_from(entries: list[Any]) -> list[Provenance]:
    out: list[Provenance] = []
    for e in entries:
        eid = getattr(e, "id", None)
        if eid is None:
            continue
        out.append(
            Provenance(
                source_type="log_entry",
                source_id=str(eid),
                description=str(_payload(e).get("message") or _event_type(e))[:120],
                weight=1.0,
            )
        )
    return out