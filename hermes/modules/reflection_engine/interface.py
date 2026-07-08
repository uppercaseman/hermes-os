"""Public entry point for the Reflection Engine.

Everything outside this package imports from here, never from
`service.py` directly -- mirrors every other module's interface.py
convention.

`ReflectionEngine` is exposed both as a concrete class and (via
`build_reflection_engine`) as the recommended factory. The factory
binds the optional collaborators (`memory`, `logs`, `candidate_extractor`,
`event_bus`, `thresholds`) with sensible defaults so a call site that
only cares about the engine's algorithm can write
`build_reflection_engine(memory=fake_memory)` and ignore the rest.
"""
from __future__ import annotations

import uuid
from typing import Protocol

from hermes.core.event_bus.interface import EventBus
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
    ReflectionEngineError,
    UnknownReflectionCandidateError,
    UnknownReflectionRunError,
)
from hermes.modules.reflection_engine.models import (
    CandidateType,
    CLOSE_CONFIDENCE_BAND,
    ConfidenceScore,
    DestinationType,
    HIGH_CONFIDENCE_THRESHOLD,
    MemoryType,
    Provenance,
    ReflectionCandidate,
    ReflectionOutcome,
    ReflectionRun,
    ReflectionThresholds,
    RiskLevel,
    all_destinations,
    claim_key,
    destination_tag,
)
from hermes.modules.reflection_engine.service import ReflectionEngine

__all__ = [
    # Service / Protocol surface
    "ReflectionEngine",
    "ReflectionEngineProtocol",
    "build_reflection_engine",
    # Models
    "ReflectionCandidate",
    "ReflectionRun",
    "ReflectionOutcome",
    "ReflectionThresholds",
    "ConfidenceScore",
    "Provenance",
    # Type aliases (re-exported so callers can `import` them from the
    # interface, not from models.py)
    "MemoryType",
    "DestinationType",
    "CandidateType",
    "RiskLevel",
    # Constants
    "HIGH_CONFIDENCE_THRESHOLD",
    "CLOSE_CONFIDENCE_BAND",
    # Helpers
    "all_destinations",
    "claim_key",
    "destination_tag",
    # Cross-module contracts
    "MemoryWriter",
    "LogQuerier",
    "WorkingMemoryReader",
    "CandidateExtractor",
    # Errors
    "ReflectionEngineError",
    "ReflectionConfigError",
    "UnknownReflectionCandidateError",
    "UnknownReflectionRunError",
    "ApprovalDeniedError",
    "CommitmentFailedError",
    "CandidateShapeError",
]


class ReflectionEngineProtocol(Protocol):
    """The Protocol other modules write against -- so a future swap of
    `ReflectionEngine` (e.g. for an LLM-backed implementation) doesn't
    require changes anywhere else.

    Mirrors the concrete class's public methods. Kept narrow on
    purpose: the engine's *internal* helpers (`_harvest`,
    `_score_candidate`, etc.) are intentionally NOT in this surface."""

    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    async def reflect(
        self,
        *,
        mission_id: uuid.UUID,
        terminal_status: str,
    ) -> ReflectionOutcome:
        ...

    async def approve_candidate(
        self,
        *,
        run_id: uuid.UUID,
        candidate_id: uuid.UUID,
        approver: str,
    ) -> ReflectionOutcome:
        ...

    async def reject_candidate(
        self,
        *,
        run_id: uuid.UUID,
        candidate_id: uuid.UUID,
        approver: str,
        reason: str,
    ) -> ReflectionOutcome:
        ...

    def get_run(self, run_id: uuid.UUID) -> ReflectionRun:
        ...

    def get_outcome(self, run_id: uuid.UUID) -> ReflectionOutcome:
        ...

    def list_runs(self) -> list[ReflectionRun]:
        ...


def build_reflection_engine(
    *,
    memory: MemoryWriter,
    logs: LogQuerier | None = None,
    working_memory: WorkingMemoryReader | None = None,
    candidate_extractor: CandidateExtractor | None = None,
    event_bus: EventBus | None = None,
    thresholds: ReflectionThresholds | None = None,
    agent_id: str = "reflection_engine",
) -> ReflectionEngine:
    """Factory mirroring the rest of the codebase. `memory` is
    required; everything else has a sensible default so test code can
    write `build_reflection_engine(memory=fake_memory)` and ignore
    the optional collaborators."""
    return ReflectionEngine(
        memory=memory,
        logs=logs,
        working_memory=working_memory,
        candidate_extractor=candidate_extractor,
        event_bus=event_bus,
        thresholds=thresholds,
        agent_id=agent_id,
    )