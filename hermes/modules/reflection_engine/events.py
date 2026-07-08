"""Event-type constants the Reflection Engine publishes.

Namespaced `reflection_engine.*`, following `Standards/Event Naming
Convention`. The Reflection Engine does NOT subscribe to its own
events -- it only publishes them. Subscribers (Logging System via the
wildcard `*`, a future Memory Galaxy UI, a future dashboard) consume
these to observe one mission's reflection pass without depending on
the engine's internals.

The naming follows the directive's request: every event the engineering
plan called out is given a constant here, scoped to the spec-defined
phase that produces it.
"""

# Phase 1 (Harvest) / lifecycle: a reflection run begins.
REFLECTION_STARTED = "reflection_engine.reflection.started"

# Phase 7 (Transition) / lifecycle: every candidate from this run has
# either been promoted, merged, superseded, or dropped -- the run is over.
REFLECTION_COMPLETED = "reflection_engine.reflection.completed"

# Phase 2 (Candidate Generation): one new candidate was produced.
MEMORY_CANDIDATE_CREATED = "reflection_engine.memory.candidate.created"

# Phase 6 (Commit), success path: one candidate was written to (or
# merged into) one of the four destination memory types via Memory
# Manager. Not emitted for Phase-4 rejections or Phase-5 denials --
# those have their own events below.
MEMORY_PROMOTED = "reflection_engine.memory.promoted"

# Phase 4 (Quality Gate) outcome: one candidate was rejected by a
# quality gate (duplicate-merge is not a rejection -- it is a MEMORY_PROMOTED
# with `action="merged"`, since the merge IS a successful write).
MEMORY_REJECTED = "reflection_engine.memory.rejected"

# Contradiction-resolution outcome: an existing entry was marked
# `superseded_by` the candidate's new entry id. The old entry is never
# deleted -- per `Memory Galaxy`'s additive-only rule, it is marked,
# not removed. A separate event from `MEMORY_PROMOTED` so subscribers
# can distinguish "wrote a new entry" from "demoted a prior entry."
MEMORY_SUPERSEDED = "reflection_engine.memory.superseded"

# Phase 5 outcome: a candidate was denied by the human approver. The
# approver's identity and the rejection reason are in the payload; the
# candidate is dropped, not retried automatically.
MEMORY_APPROVAL_DENIED = "reflection_engine.memory.approval_denied"

# Phase 5 outcome: a candidate was approved by the human approver.
# Surfaced so subscribers can confirm approvals resolved without
# waiting for the eventual `MEMORY_PROMOTED`.
MEMORY_APPROVAL_GRANTED = "reflection_engine.memory.approval_granted"

# Catch-all failure: an unexpected exception escaped one of the seven
# phases. The mission is held in its terminal pre-Dissolved state by
# design -- this event is for observability, not for a retry trigger
# the engine itself would own.
REFLECTION_FAILED = "reflection_engine.reflection.failed"
