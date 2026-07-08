"""Reflection Engine-specific exception types.

These names are deliberately narrow and descriptive. Every one maps
directly to a Phase-4 quality-gate failure mode defined by
`Specification/02 - Cognitive Architecture/Reflection Engine` /
`ADR-0015` -- no new exception categories were introduced.
"""
from __future__ import annotations

import uuid


class ReflectionEngineError(Exception):
    """Base class for everything this module raises. Lets callers catch
    Reflection-domain failures without taking down unrelated code
    paths."""


class ReflectionConfigError(ReflectionEngineError):
    """The engine was constructed without a required collaborator
    (Memory Manager, Logging System, Event Bus) and a method that needs
    it was called. Mirrors `MissionSystemConfigError`'s posture in
    `mission_system/errors.py` -- fail loud at the boundary, never
    silently swallow the missing dependency."""


class UnknownReflectionCandidateError(ReflectionEngineError):
    """A caller referenced a `ReflectionCandidate` by id that the engine
    has no record of. Distinct from the same name elsewhere because
    this one operates on the engine's local candidate-id namespace,
    not on Memory Manager's `MemoryEntry.id` namespace."""


class UnknownReflectionRunError(ReflectionEngineError):
    def __init__(self, mission_id: uuid.UUID) -> None:
        self.mission_id = mission_id
        super().__init__(
            f"no reflection run has been started for mission {mission_id} "
            f"(or the previous run completed and its records were cleared)"
        )


class ApprovalDeniedError(ReflectionEngineError):
    """A human approver rejected a candidate via `approve_candidate(...,
    approved=False)`. The caller is expected to log + drop per Phase-5
    rules. Distinct from "candidate below threshold" which is *not* an
    error -- the engine drops those silently with a logged reason."""


class CommitmentFailedError(ReflectionEngineError):
    """The Phase-6 atomic commit failed (one or more Memory Manager
    writes raised). Per `Reflection Engine` Phase-7 / `Memory Galaxy`'s
    additive-only rule, the engine does not retry mid-commit; the
    mission is held in its terminal pre-Dissolved state and the caller
    is expected to invoke `reflect(...)` again -- reflection is
    idempotent for un-committed work."""


class CandidateShapeError(ReflectionEngineError):
    """A candidate was submitted (e.g. via Phase-3 scoring) that
    violates a contractual invariant -- e.g. an empty claim, a
    confidence outside `[0.0, 1.0]`, or a `type` outside the six
    permitted values. Caught at the phase boundary so a bad harvest
    doesn't silently pollute the candidate set."""
