"""Reflection Engine -- turns a mission's transient experience into
durable Memory Galaxy entries.

Public surface mirrors every other module in `hermes/modules/`: import
from this package, never from `service.py` directly.

  >>> from hermes.modules.reflection_engine import (
  ...     ReflectionEngine,
  ...     ReflectionEngineProtocol,
  ...     build_reflection_engine,
  ...     ReflectionCandidate,
  ...     ReflectionRun,
  ...     ReflectionOutcome,
  ... )
"""
from hermes.modules.reflection_engine import events as events_module
from hermes.modules.reflection_engine.errors import (
    ApprovalDeniedError,
    CandidateShapeError,
    CommitmentFailedError,
    ReflectionConfigError,
    ReflectionEngineError,
    UnknownReflectionCandidateError,
    UnknownReflectionRunError,
)
from hermes.modules.reflection_engine.interface import (
    ReflectionEngineProtocol,
    build_reflection_engine,
)
from hermes.modules.reflection_engine.models import (
    CLOSE_CONFIDENCE_BAND,
    HIGH_CONFIDENCE_THRESHOLD,
    CandidateType,
    ConfidenceScore,
    DestinationType,
    GateVerdict,
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

# Re-export event-type constants at the top level so callers can
# `from hermes.modules.reflection_engine import MEMORY_PROMOTED`.
# The `events` submodule remains the canonical home.
MEMORY_APPROVAL_DENIED = events_module.MEMORY_APPROVAL_DENIED
MEMORY_APPROVAL_GRANTED = events_module.MEMORY_APPROVAL_GRANTED
MEMORY_CANDIDATE_CREATED = events_module.MEMORY_CANDIDATE_CREATED
MEMORY_PROMOTED = events_module.MEMORY_PROMOTED
MEMORY_REJECTED = events_module.MEMORY_REJECTED
MEMORY_SUPERSEDED = events_module.MEMORY_SUPERSEDED
REFLECTION_COMPLETED = events_module.REFLECTION_COMPLETED
REFLECTION_FAILED = events_module.REFLECTION_FAILED
REFLECTION_STARTED = events_module.REFLECTION_STARTED

__all__ = [
    # Service + factory
    "ReflectionEngine",
    "ReflectionEngineProtocol",
    "build_reflection_engine",
    # Errors
    "ReflectionEngineError",
    "ReflectionConfigError",
    "UnknownReflectionCandidateError",
    "UnknownReflectionRunError",
    "ApprovalDeniedError",
    "CommitmentFailedError",
    "CandidateShapeError",
    # Models
    "ReflectionCandidate",
    "ReflectionRun",
    "ReflectionOutcome",
    "ReflectionThresholds",
    "ConfidenceScore",
    "GateVerdict",
    "Provenance",
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
    # Event-type constants (also reachable via `.events`)
    "REFLECTION_STARTED",
    "REFLECTION_COMPLETED",
    "MEMORY_CANDIDATE_CREATED",
    "MEMORY_PROMOTED",
    "MEMORY_REJECTED",
    "MEMORY_SUPERSEDED",
    "MEMORY_APPROVAL_GRANTED",
    "MEMORY_APPROVAL_DENIED",
    "REFLECTION_FAILED",
    "events_module",
]