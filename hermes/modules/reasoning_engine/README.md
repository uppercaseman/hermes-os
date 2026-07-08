# Reasoning Engine

> **Sprint-3 — Knowledge & Reasoning Layer**

The Reasoning Engine prepares structured `ReasoningContext` payloads
for Commander (and a future Provider Ecosystem layer). It does
**not** call AI models or perform provider reasoning in Sprint-3 --
that belongs to the Provider Ecosystem layer, which is out of scope
per the directive.

## Sprint-3 scope

| In scope | Out of scope |
| -------- | ------------ |
| Take `ReasoningRequest` (intent + seed set + mission) | Call AI / provider reasoning |
| Hand off to `ContextBuilder.assemble(...)` | Modify Commander service internals |
| Freeze the assembled entries + scoring trace into `ReasoningContext` | Write to Memory |
| Publish `REASONING_PREPARED` event | Persist reasoning outputs |
| Bind `MemoryResolver` slot in Commander via `build_default_memory_resolver(...)` | Anything beyond payload preparation |

A guard rail: if a caller asks the Engine to perform provider
reasoning directly (e.g. `mode != "assemble"`), it raises
`ProviderReasoningUnavailableError` so the misuse is loud, not silent.

## Inputs and outputs

```python
from hermes.modules.reasoning_engine import (
    ReasoningEngine,
    build_reasoning_engine,
    ReasoningRequest,
    ReasoningContext,
    build_default_memory_resolver,
)

engine = build_reasoning_engine(context_builder=ctx_builder)
context = await engine.prepare(ReasoningRequest(
    requesting_agent_id="commander",
    seed_ids=[skill_entry.id, experience_entry.id],
    intent="synthesize a budget-alert recommendation",
    mission_id=uuid.uuid4(),
    max_entries=8,
))
# context.entries -- MemoryEntry list ordered by score descending
# context.context_scores -- matching per-entry scores
# context.trace -- audit trail (request, assembled ids, scores)
# context.intent, context.mode
```

## Commander `MemoryResolver` binding

```python
from hermes.modules.reasoning_engine import (
    ReasoningEngine,
    build_reasoning_engine,
    build_default_memory_resolver,
)

engine = build_reasoning_engine(context_builder=ctx_builder)
resolver = build_default_memory_resolver(reasoning_engine=engine)
# `resolver` has the shape of Commander's `MemoryResolver` Protocol:
#   async def resolve(intent, workflow) -> MemoryRequirement
# Wire it into Commander's collaborator bag without an interface change.
```

The default binding:
1. Reads `intent.slots["seed_memory_ids"]` (a list of uuid strings).
2. Calls `ReasoningEngine.prepare(...)`.
3. Translates the snapshot into a `MemoryRequirement` whose
   `keys` are the assembled entry ids and whose `scope` mirrors
   the top entry.

## Public surface

```python
from hermes.modules.reasoning_engine import (
    # Engine + factory
    ReasoningEngine,
    ReasoningEngineProtocol,
    build_reasoning_engine,
    # Commander binding
    build_default_memory_resolver,
    # Models
    ReasoningRequest,
    ReasoningContext,
    ReasoningTrace,
    ReasoningMode,
    # Contracts
    ContextSource,
    ReasoningSink,
    # Errors
    ReasoningEngineError,
    ReasoningConfigError,
    EmptyReasoningContextError,
    ProviderReasoningUnavailableError,
    # Events
    REASONING_PREPARED,
    REASONING_PREPARATION_FAILED,
)
```

## Backwards compatibility

- No Memory Manager change
- No Reflection Engine change
- No Knowledge Graph change
- No Context Builder change
- No Commander change — `MemoryResolver` binding is implemented as
  a factory helper (`build_default_memory_resolver`); Commander
  service internals stay untouched

## Events

| Constant | Fires when |
| -------- | ---------- |
| `REASONING_PREPARED` | A `ReasoningContext` was prepared successfully |
| `REASONING_PREPARATION_FAILED` | Preparation could not produce a non-empty context |

Publishing is best-effort — a bus failure does not fail the read-
only preparation that produced the event.

## Folder structure

```
hermes/modules/reasoning_engine/
├── README.md
├── __init__.py
├── interface.py            <- Engine + factory + Protocol + build_default_memory_resolver
├── service.py              <- ReasoningEngine runtime
├── models.py               <- ReasoningRequest, ReasoningContext, ReasoningTrace, ReasoningMode
├── contracts.py            <- ContextSource, ReasoningSink, ReasoningEngineProtocol
├── events.py               <- 2 event constants
├── errors.py               <- 4 exception types
└── tests/test_service.py   <- ~24 tests
```

## Out of scope (next sprint candidates)

- Real provider reasoning (call into an LLM) -- Provider Ecosystem
  layer, future sprint.
- Persistent `ReasoningContext` snapshots (today the Engine is
  read-only and produces the snapshot inline).
- Reasoning Engine writing memory back -- the spec defines the
  Engine as preparation, so promotion remains the Reflection
  Engine's job.