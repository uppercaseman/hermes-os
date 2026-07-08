# Reflection Engine

> **Mission-driven operating system kernel · ADR-0015**

The Reflection Engine is the **single writer** to User DNA, Skill
Memory, Experience Memory, and Project Memory. It implements the
seven-phase reflection pipeline defined in
`Specification/02 - Cognitive Architecture/Reflection Engine.md` and
adopted by `ADR-0015`. This module is the only path through which
those four destination memory types are populated by Hermes; all
other code paths (Commander, Mission System, Logging System, etc.)
write only to Working Memory, Mission Memory, and the
Knowledge Graph.

## The seven phases

1. **Harvest** — read Working Memory for the mission, log history
   via the Logging System's `query(mission_id=...)`, and any
   mission-scoped entries via Memory Manager.
2. **Candidate Generation** — produce candidate lessons. The default
   extractor (`service.py:DefaultCandidateExtractor`) emits candidates
   for error-recovery sequences, user-feedback events, repeated tool
   use, and project-scoped decisions. An LLM-backed extractor
   implementing `CandidateExtractor` can replace it without engine
   changes.
3. **Scoring & Routing** — every candidate gets a confidence /
   scope_fit / risk triple. Skill Memory requires either ≥2
   contributing missions OR one prior mission with confidence ≥0.9
   AND refinement context. Single-occurrence patterns are demoted to
   Experience Memory.
4. **Quality Gates** — provenance, scope, duplicate detection,
   contradiction detection, threshold, and risk gates. The
   contradiction sub-case branches on the existing entry's
   confidence (HIGH ≥ 0.9 → approval; lower → candidate wins and
   existing is marked superseded).
5. **Human Approval** — every user_preference, every contradiction,
   every high-risk candidate, and every medium-risk candidate below
   0.7 confidence waits for `approve_candidate` /
   `reject_candidate`. No batch approval — one candidate, one
   decision.
6. **Commit** — write through Memory Manager's `record(...)`. Skill
   entries carry the contributing-mission list in the key so two
   skills that share a claim string don't collide. Supersession is
   done via `mark_superseded(...)` AFTER the new write succeeds —
   the old entry remains active until the new one is durable, per
   `Memory Galaxy`'s additive-only rule.
7. **Transition** — publish `reflection_engine.reflection.completed`
   with the outcome's `pending_approvals` count. Mission System
   listens for this event and gates its terminal → Dissolved
   transition on it.

## Public surface

```python
from hermes.modules.reflection_engine import (
    # Engine + factory
    ReflectionEngine,
    ReflectionEngineProtocol,
    build_reflection_engine,
    # Errors
    ReflectionEngineError,
    ReflectionConfigError,
    UnknownReflectionCandidateError,
    UnknownReflectionRunError,
    ApprovalDeniedError,
    CommitmentFailedError,
    CandidateShapeError,
    # Models
    ReflectionCandidate,
    ReflectionRun,
    ReflectionOutcome,
    ReflectionThresholds,
    ConfidenceScore,
    Provenance,
    GateVerdict,
    # Helpers
    all_destinations,
    claim_key,
    destination_tag,
    # Event-type constants (also reachable via `.events`)
    REFLECTION_STARTED,
    REFLECTION_COMPLETED,
    MEMORY_CANDIDATE_CREATED,
    MEMORY_PROMOTED,
    MEMORY_REJECTED,
    MEMORY_SUPERSEDED,
    MEMORY_APPROVAL_GRANTED,
    MEMORY_APPROVAL_DENIED,
    REFLECTION_FAILED,
)
```

`build_reflection_engine(...)` is the recommended factory. It mirrors
the rest of `hermes/modules/`'s constructor pattern: `memory` is
required (the engine has no useful default), everything else has a
sensible default.

```python
from hermes.modules.reflection_engine import build_reflection_engine

engine = build_reflection_engine(
    memory=memory_manager,           # required: MemoryWriter
    logs=logging_system,             # optional: LogQuerier
    working_memory=memory_manager,   # optional: WorkingMemoryReader
    candidate_extractor=my_extractor,# optional: CandidateExtractor
    event_bus=event_bus,             # optional: EventBus
    thresholds=ReflectionThresholds(...),  # optional
)

await engine.start()
outcome = await engine.reflect(mission_id=mid, terminal_status="completed")
if outcome.requires_human_action:
    for cid in outcome.pending_approvals:
        ...  # present to a human via your dashboard / CLI
```

## Events

| Constant                          | Published when                                  |
| --------------------------------- | ----------------------------------------------- |
| `REFLECTION_STARTED`              | Phase 1 begins                                  |
| `MEMORY_CANDIDATE_CREATED`        | Phase 2 emits a new candidate                   |
| `MEMORY_PROMOTED`                 | Phase 6 commits a candidate (or merges it)      |
| `MEMORY_SUPERSEDED`               | An existing entry was marked superseded_by       |
| `MEMORY_REJECTED`                 | Phase 4 dropped a candidate at a gate           |
| `MEMORY_APPROVAL_GRANTED`         | Phase 5 approved a candidate                    |
| `MEMORY_APPROVAL_DENIED`          | Phase 5 rejected a candidate                    |
| `REFLECTION_COMPLETED`            | Phase 7 — every candidate resolved              |
| `REFLECTION_FAILED`               | An unexpected exception escaped a phase         |

The engine does NOT subscribe to its own events — only publishes.
Logging System consumes via the wildcard `"*"` and stores them in
the mission's log history.

## Architectural notes

The engine was built under two constraints documented in
`Architecture/Change Policy`: no architectural change may occur
without an ADR. One conflict surfaced during Sprint-2 and was
resolved in this sprint; one remains and is recorded in
`Reflection Engine Engineering Report` as a recommended ADR.

### Sprint-2: typed memory writes (resolves Sprint-1's C1)

Sprint-1 routed the four destination memory types through
`scope="persistent"` + tags + `value` payload. Sprint-2 added
first-class typed fields to `MemoryEntry` (`memory_type`,
`confidence`, `importance`, `provenance`, `superseded_by`,
`relationships`) and a typed write path (`record_typed`) on
`MemoryManager`. The engine's `_commit_candidate` now writes
through `record_typed` so the canonical store is the typed
`memory_type` field, not a tag encoding.

The engine's public surface (`MemoryWriter` Protocol) gained
`record_typed(...)` as a structural addition. The legacy
`record(...)` method is preserved (compatible with any future
alternative Memory Manager that hasn't migrated).

Engine vocabulary vs. canonical MemoryType:

| Engine `DestinationType` | Canonical `MemoryType`  |
| ------------------------ | ----------------------- |
| `user_dna`               | `user_dna`              |
| `skill`                  | `skill_memory`          |
| `experience`             | `experience_memory`     |
| `project`                | `project_memory`        |

Working Memory, Mission Memory, Project Memory (the new
`project_memory` form is canonical), and User DNA / Skill /
Experience memories written outside the engine use the
canonical vocabulary directly.

The legacy tag encoding (`reflection_engine:managed`,
`reflection:<destination>`) is preserved alongside the typed
fields so any consumer that hasn't migrated yet still finds
engine-written entries. `migrate_memory_galaxy()` (in
`hermes.modules.memory_manager.migration`) lifts legacy
encoding to typed fields idempotently.

### C2 — Mission System does not publish a `cancelled` event

Mission System publishes `mission_system.mission.completed` and
`mission_system.mission.failed` only. There is no
`mission_system.mission.cancelled` event today.

**Resolution:** the engine subscribes to the two existing events.
Cancelled missions reach the engine only through a manual operator
hook (`reflect(mission_id=..., terminal_status="cancelled")`) and
get reduced-form reflection (no Skill Memory or User DNA
promotion). See `service.py:_run_phases`'s `cancelled_skip` branch.

**Recommended ADR:** add `mission_system.mission.cancelled` to
Mission System's event vocabulary and a Dissolved guard so cancelled
missions get the same lifecycle treatment as failed missions.

## Wiring into the kernel

The engine is wired into the Hermes kernel by Commander via the
standard module-bootstrap path. Two integration points:

1. **Mission System → Reflection Engine (terminal trigger).**
   `start()` subscribes to `mission_system.mission.completed` and
   `mission_system.mission.failed`. When a mission reaches either
   terminal state, Mission System publishes the event; the engine
   triggers a reflection pass automatically.

2. **Reflection Engine → Mission System (Dissolved gate).**
   `REFLECTION_COMPLETED` carries `pending_approvals` in the
   payload. Mission System should hold the mission in its
   pre-Dissolved state when `pending_approvals > 0` and only
   transition to Dissolved after the count reaches 0. The Mission
   System code that handles this is intentionally not in this
   module — per `Standards/Module Layout`, modules depend on the
   EventBus, not on each other.

A future Sprint should add a `ReflectionTrigger` adapter that
re-runs reflection on historical missions from a backfill queue.

## Configuration

`ReflectionThresholds` is the only configurable surface. Defaults
match `Reflection Engine`'s "Quality Thresholds" table verbatim:

| Destination      | Confidence floor | Other                                |
| ---------------- | ---------------- | ------------------------------------ |
| `user_dna`       | ≥ 0.7            | always human-approved                |
| `skill`          | ≥ 0.8            | ≥ 2 contributing missions; OR        |
|                  |                  | 1 mission + confidence ≥ 0.9         |
|                  |                  | + refinement context                 |
| `experience`     | ≥ 0.5            | —                                    |
| `project`        | ≥ 0.6            | —                                    |

Override per-engine:

```python
from hermes.modules.reflection_engine import build_reflection_engine, ReflectionThresholds

engine = build_reflection_engine(
    memory=memory_manager,
    thresholds=ReflectionThresholds(
        user_dna_min=0.8,
        skill_min=0.85,
        skill_min_missions=3,
    ),
)
```

## Tests

`tests/test_service.py` covers the directive's required scenarios:

- Normal flow (empty harvest; one experience_case; user_feedback
  with approval)
- Quality gates (threshold, provenance, scope)
- Duplicates (near-duplicate merge; second-reflect idempotency)
- Contradictions (high-confidence → approval;
  low-confidence → supersede)
- Approval (approve after reflect; reject; reject-non-required
  raises; unknown candidate raises)
- Event publication (lifecycle; rejection event; supersession
  event; mission-terminal-event trigger; mission-failed-event
  trigger)
- Mission cancellation (skill/user_dna dropped; project retained)
- Error recovery (extractor crash → REFLECTION_FAILED; per-candidate
  isolation; commit failure → rejected candidate + completed run)
- Idempotency (repeated reflect for the same mission returns the
  same outcome)
- Skill Memory routing (single mission → demoted to experience;
  two missions → skill)
- Surface (Protocol round-trip; unknown-run / unknown-outcome
  lookups; invalid terminal status)

36 tests, ~0.4s. Run:

```bash
python3 -m pytest hermes/modules/reflection_engine -q
```

## Operational notes

- **Idempotency:** `reflect(mission_id, terminal_status)` returns
  the same outcome on every subsequent call for a finalised run.
  Pre-finalised runs are NOT idempotent — call once per terminal
  event. The `event_bus.subscribe` pattern delivers events at-least-
  once; the engine's idempotency check protects against duplicate
  delivery.

- **Per-candidate isolation:** a failure on one candidate (extractor
  crash, commit failure, malformed provenance) does not abort the
  other candidates. The failed candidate is recorded with a
  `rejection_reason` and the run finalises successfully.

- **Supersession ordering:** Phase 4 detects contradictions and
  sets `candidate.superseded_entry`; Phase 6 writes the new entry
  FIRST, THEN calls `mark_superseded(...)` on the old entry. A
  Phase 6 failure mid-write leaves the old entry active (which is
  the additive-only guarantee from `Memory Galaxy`).

- **Logging:** every gate decision emits a `MEMORY_REJECTED` event
  AFTER the run's candidate loop completes, so subscribers see
  rejections in the same order they happened, and every rejection
  precedes `REFLECTION_COMPLETED`.

- **No batch approval:** `approve_candidate` and `reject_candidate`
  operate on one candidate at a time, by `candidate_id`. A future
  dashboard or CLI is responsible for iterating
  `outcome.pending_approvals` and resolving them — the engine never
  approves more than one candidate per call.

## See also

- `Specification/02 - Cognitive Architecture/Reflection Engine.md` —
  the canonical spec
- `ADR-0015 - Adopt the Reflection Pipeline as the Memory Promotion
  Authority.md` — the architecture decision
- `Standards/Module Layout.md` — the eight-file module convention
- `Standards/Event Naming Convention` — `reflection_engine.*` is
  the engine's event-bus namespace
- `Memory Galaxy` and `Memory Manager` specifications — the
  destination-side constraints the engine respects