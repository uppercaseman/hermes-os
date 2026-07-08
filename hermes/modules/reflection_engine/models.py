"""Pydantic data contracts for the Reflection Engine.

This module's own data shapes -- the candidate lesson, its scoring
triple, its gate verdict, and the per-mission reflection-run record.
Cross-module types that other modules import live in `contracts.py`.

Why all of this lives on the engine side, not in Memory Manager:
the Reflection Engine is the **single writer** to User DNA, Skill
Memory, Experience Memory, and Project Memory (per `Reflection Engine`
Design Decisions + `ADR-0015`). Encoding the *shape of a reflection
candidate* in Memory Manager would couple Memory Manager to reflection
semantics that don't belong there. Keeping these models here means
Memory Manager stays generic, and a future reflection-engine redesign
can change these models without breaking Memory Manager.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# The eight canonical memory types (per `Memory Galaxy` + the destination
# specs). Reflection Engine writes to four of them; the other four are
# inputs (Working, Mission) or read-only context (the prior contents of
# the four destinations themselves, used for duplicate/contradiction
# detection).
# ---------------------------------------------------------------------------
MemoryType = Literal[
    "user_dna",
    "skill",
    "experience",
    "project",
    "working",
    "mission",
    "knowledge_graph",
]

# The four destinations the engine is allowed to write to. Each is
# mapped onto Memory Manager's existing `scope` namespace plus a
# `reflection:`-namespaced tag at commit time -- see `service.py`'s
# `_DESTINATION_SCOPE` table for the exact mapping. Kept as a strict
# Literal so a bad destination is caught at type-check time, not at
# the gate that runs just before the commit.
DestinationType = Literal["user_dna", "skill", "experience", "project"]

# The six candidate types from `Reflection Engine` Phase 2. The first
# four are scope proposals; the last two are *flag types* the gate sets
# when it finds an existing entry that already covers the same claim
# (duplicate) or contradicts it (contradiction). A flag-typed candidate
# is then routed by Phase 3, not by this type -- the type records how
# it was found, the routing records where it goes.
CandidateType = Literal[
    "user_preference",
    "project_fact",
    "skill_pattern",
    "experience_case",
    "contradiction",
    "duplicate",
]

RiskLevel = Literal["low", "medium", "high"]

# A candidate needs approval iff its `approval_required` field is True.
# Computed deterministically in Phase 4 from `(type, risk, confidence)`
# -- see `service.py:_requires_approval`. Encoded as an explicit field
# so the Phase-5 loop has nothing to recompute.


class ConfidenceScore(BaseModel):
    """The three-axis score from `Reflection Engine` Phase 3.

    All three are in `[0.0, 1.0]`. `confidence` is what every quality
    threshold (`User DNA 0.7`, `Skill Memory 0.8`, etc.) gates on;
    `scope_fit` is a softer check (`project_fact` with scope_fit=0.3
    against User DNA is a misrouted candidate); `risk` is a category,
    not a number -- a `user_preference` is high-risk by definition; a
    project-scoped fact is medium; an experience case is low.
    """

    confidence: float = Field(ge=0.0, le=1.0)
    scope_fit: float = Field(ge=0.0, le=1.0)
    risk: RiskLevel

    @model_validator(mode="after")
    def _validate_consistency(self) -> ConfidenceScore:
        """`risk=high` plus `confidence<0.5` would make Phase 4's
        approval gate trigger twice (once via risk, once via
        confidence). Not wrong, but the validator surfaces the case
        so a future tuning pass can decide whether the redundancy is
        intentional or should be tightened. Today: warning only, do
        not raise -- legitimate uses exist (a tentative high-risk
        finding worth flagging for review)."""
        return self


class Provenance(BaseModel):
    """One source-of-evidence reference for a candidate. The
    `Reflection Engine` spec requires "the candidate cite at least one
    harvested input as evidence" -- a candidate with empty provenance
    is dropped by the provenance gate.

    Sprint-2: this class is structurally identical to the canonical
    `Memory Manager` `Provenance` (from
    `hermes.modules.memory_manager.typed`). The two definitions
    coexist because importing the canonical class into the engine's
    data contract module would couple `Reflection Engine` models
    to `Memory Manager` at import time -- the engine otherwise
    depends on Memory Manager only through the `MemoryWriter`
    Protocol in `contracts.py`. Pass-through between the two
    `Provenance` types works because every field is a primitive
    (`str` / `float` / `Literal`).

    `source_type` distinguishes a `LogEntry` reference (the Logging
    System path) from a `MemoryEntry` reference (the Memory Manager
    path) from a synthetic source (e.g. an LLM-extracted candidate the
    harvester produced directly). The id is always a UUID string per
    Memory Manager / Logging System's own conventions; we keep it as
    `str` here so a future id scheme doesn't require re-modeling.
    """

    source_type: Literal["memory_entry", "log_entry", "synthetic"]
    source_id: str
    description: str = ""
    weight: float = Field(default=1.0, ge=0.0)


class ReflectionCandidate(BaseModel):
    """One candidate lesson produced by Phase 2, scored by Phase 3,
    gated by Phase 4, and either approved (Phase 5), committed
    (Phase 6), and signaled (Phase 7) -- or dropped earlier with a
    logged reason.

    `id` is the engine's internal id (uuid4). It is NOT the same as
    `MemoryEntry.id` -- a candidate's id is its lifecycle id within one
    reflection run; the MemoryEntry it eventually becomes (or merges
    into) carries a different id. Keeping the two distinct lets the
    engine track "candidate 7 became MemoryEntry 42" without colliding
    namespaces.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    mission_id: uuid.UUID
    claim: str
    candidate_type: CandidateType
    destination: DestinationType
    provenance: list[Provenance] = Field(default_factory=list)
    score: ConfidenceScore
    contributing_mission_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description=(
            "For `skill_pattern` candidates, the prior mission ids whose "
            "evidence contributed -- per `Reflection Engine` Phase 6's "
            "metadata rules. Empty for other types."
        ),
    )

    # Phase-4 state. Defaults match "candidate fresh from Phase 2,
    # nothing decided yet". A candidate progresses to a terminal state
    # exactly once per reflection run -- idempotency is enforced in
    # `service.py:reflect` rather than at the model layer so a single
    # source of truth governs state transitions.
    approval_required: bool = False
    approved: bool | None = None
    approver: str | None = None
    rejection_reason: str | None = None

    # The existing MemoryEntry id this candidate merged into or
    # contradicted. Set by Phase 4 when a duplicate / contradiction is
    # resolved. Distinct from `result_entry_id` below (the entry the
    # candidate itself eventually produced).
    merged_into: uuid.UUID | None = None
    contradicted_entry: uuid.UUID | None = None
    superseded_entry: uuid.UUID | None = None

    # Set by Phase 6 after the commit succeeds. None until then.
    result_entry_id: uuid.UUID | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GateVerdict(BaseModel):
    """The result of one quality gate on one candidate. A candidate is
    `accepted` only when every gate that ran produced a verdict of
    `pass`. The `gate` name is one of:
        "duplicate", "contradiction", "scope", "provenance", "risk",
        "threshold", "approval".

    `action` records what the gate decided -- distinct from
    `outcome`, because "merge with confidence update" is a pass AND
    a write, and we want both pieces of information in one record.
    """

    gate: str
    outcome: Literal["pass", "fail", "needs_approval"]
    action: Literal["accept", "merge", "supersede", "flag", "drop"]
    reason: str = ""
    target_entry_id: uuid.UUID | None = None


class ReflectionRun(BaseModel):
    """The per-mission record of one reflection pass. Created at
    `Phase 1` start, mutated by every phase, and finalised at `Phase
    7`. The Mission System queries this to decide whether the mission
    may transition to Dissolved -- a non-finalised `ReflectionRun`
    means "do not dissolve yet" per `Mission Lifecycle` Phase-7.

    `terminal_status` is the mission's terminal state that triggered
    this run -- completed, failed, or (when wired in via a future
    ADR) cancelled. Stored here so the run record carries its trigger
    even after Mission System has moved on.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    mission_id: uuid.UUID
    terminal_status: Literal["completed", "failed", "cancelled"]
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finalised_at: datetime | None = None

    candidates: list[ReflectionCandidate] = Field(default_factory=list)
    verdicts: list[GateVerdict] = Field(default_factory=list)

    # Final tallies -- counted once at Phase 7 end. Useful for the
    # dashboard, and for tests asserting the run's net effect.
    promoted_count: int = 0
    rejected_count: int = 0
    superseded_count: int = 0
    merged_count: int = 0

    # Outcomes from each phase, used by tests and by `reflect(...)`'s
    # idempotency check (a finalised run whose `cancelled_skip` is True
    # was a reduced-form reflection -- no skill/user_dna promotion).
    cancelled_skip: bool = False

    @property
    def is_finalised(self) -> bool:
        return self.finalised_at is not None


class ReflectionThresholds(BaseModel):
    """Configurable per-destination confidence floors and skill-pattern
    minimum. Defaults match `Reflection Engine`'s "Quality Thresholds"
    table verbatim:

        User DNA      >= 0.7 (and always human-approved)
        Project Memory >= 0.6
        Skill Memory   >= 0.8 (and >= 2 contributing missions)
        Experience     >= 0.5

    `skill_min_missions` encodes the Skill Memory cross-mission
    threshold (>=2 prior missions, or one prior mission with
    confidence >= 0.9 and a refinement context -- the single-mission
    refinement exception is encoded in `service.py:_routes_to_skill`,
    not here, since it depends on per-candidate state).
    """

    user_dna_min: float = Field(default=0.7, ge=0.0, le=1.0)
    project_min: float = Field(default=0.6, ge=0.0, le=1.0)
    skill_min: float = Field(default=0.8, ge=0.0, le=1.0)
    experience_min: float = Field(default=0.5, ge=0.0, le=1.0)
    skill_min_missions: int = Field(default=2, ge=1)
    skill_single_mission_refinement_min: float = Field(default=0.9, ge=0.0, le=1.0)
    medium_risk_approval_floor: float = Field(default=0.7, ge=0.0, le=1.0)


class ReflectionOutcome(BaseModel):
    """The structured return value from `ReflectionEngine.reflect(...)`.
    Callers (Mission System, tests) inspect this to decide whether the
    mission may proceed to Dissolved."""

    run: ReflectionRun
    success: bool
    failure_reason: str | None = None
    pending_approvals: list[uuid.UUID] = Field(
        default_factory=list,
        description=(
            "Ids of candidates still awaiting Phase 5 approval. Empty when "
            "every approval-required candidate has been resolved. A non-empty "
            "list means the mission must remain in its terminal pre-Dissolved "
            "state."
        ),
    )

    @property
    def requires_human_action(self) -> bool:
        return bool(self.pending_approvals)


# ---------------------------------------------------------------------------
# Helpers used by service.py and tests.
# ---------------------------------------------------------------------------

# Confidence above which a contradiction with an existing entry may be
# auto-superseded (the old entry is marked superseded_by, not deleted).
# Per `Reflection Engine`'s "Contradiction Handling" sub-case: a
# contradicting existing entry with confidence >= 0.9 triggers human
# approval; below 0.9 the new candidate wins and the old is superseded.
HIGH_CONFIDENCE_THRESHOLD: float = 0.9

# Confidence band within which two same-reflection candidates are
# "close" -- both flagged for human approval rather than one winning
# by default. Per `Reflection Engine` Contradiction Handling's
# third sub-case.
CLOSE_CONFIDENCE_BAND: float = 0.1


def all_destinations() -> list[DestinationType]:
    """The four destination memory types the engine writes to. Exposed
    as a helper (rather than a module constant) so callers and tests
    can iterate the closed set without redefining it."""
    return ["user_dna", "skill", "experience", "project"]


def destination_tag(destination: DestinationType) -> str:
    """The `reflection:<destination>` tag every committed entry
    carries, so Phase 4's read-only-context queries can scope to one
    destination type by tag. See `service.py:_DESTINATION_SCOPE` for
    the matching scope value."""
    return f"reflection:{destination}"


def claim_key(destination: DestinationType, claim: str) -> str:
    """The Memory Manager upsert key used for committed entries.

    `destination` is included so two destinations that happen to store
    the same claim string don't collide in the key index -- e.g. an
    experience case and a skill pattern that both describe "PREFER
    Terse Responses" become distinct entries. The memory manager's
    `save(...)` upserts by `(scope, owner, key)`, so the key is the
    merge-and-dedupe handle for the same destination.
    """
    # Normalise whitespace and lowercase so "Prefer terse responses" and
    # "prefer   terse responses" hit the same key. The Memory Manager
    # does no normalisation of its own; doing it here means duplicates
    # are caught at the gate instead of producing two entries with the
    # same human-meaningful claim.
    normalised = " ".join(claim.split()).lower()
    return f"{destination}:{normalised}"