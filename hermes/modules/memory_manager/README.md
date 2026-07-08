# Hermes Memory Manager

Structured, permissioned, taggable memory for Hermes. Every named memory
category from the brief maps onto **one model** (`MemoryEntry`) and a
small set of operations, rather than seven parallel stores.

## The model: two orthogonal dimensions, not seven categories

```
                    owner_agent_id
                    ───────────────
                    None (shared)     "agent-x" (private)
scope   session   │ shared session   │ agent-x's session notes
        persistent│ shared project   │ agent-x's private project notes
        workflow   │ shared run state │ agent-x's private run state
        decision    │           (always owner_agent_id = the deciding agent)
        error        │           (always owner_agent_id = the erroring agent)
```

- **`scope`** answers "when/where does this live": `session` (short-term
  conversation), `persistent` (long-term project), `workflow` (one
  workflow run), `decision`/`error` (append-only audit history).
- **`owner_agent_id`** answers "who owns this": `None` means shared, a
  value means private to that agent.

"Agent memory" is deliberately **not** a fourth scope value — forcing it
to be one would mean either duplicating session/persistent/workflow
logic a fourth time, or picking an arbitrary default lifecycle for it.
It's simpler and more correct to recognize that any scope can be
agent-owned or shared; that's exactly what `owner_agent_id` already
expresses.

## Requirement → mechanism

| Requirement | Mechanism |
|---|---|
| Short-term conversation memory | `scope="session"`, `session_id` set, usually with `ttl_seconds` |
| Long-term project memory | `scope="persistent"` |
| Agent memory | any scope with `owner_agent_id` set |
| Workflow memory | `scope="workflow"`, `workflow_run_id` set |
| Decision history | `record_decision()` / `get_decision_history()` |
| Error history | `record_error()` / `get_error_history()` |
| Obsidian vault integration | `markdown.py` (real) + `ObsidianVaultAdapter` (placeholder) |
| Future vector search | `VectorSearchProvider` protocol + `search_similar()` |
| Tagging | `MemoryEntry.tags`, AND-matched in `query()` |
| Backlinks | `MemoryEntry.backlinks` + `get_backlinks()` (the reverse lookup) |
| Memory permissions per agent | `MemoryPermissionGrant` + `grant_permission`/`revoke_permission` |
| Save/retrieve structured memories | `save()` (upserts), `get()`/`get_by_key()`/`query()` |

## Why decision/error history can't go through `save()`

`save()` **upserts** by a composite key of (scope, owner, session/workflow,
key) — calling it twice with the same key replaces the value, which is
exactly the behavior you want for session/persistent/workflow memory. A
history log must never behave that way: recording the same decision
twice should produce two entries, not one overwritten entry. So `save()`
explicitly raises `ValueError` for `scope in ("decision", "error")`, and
`record_decision()`/`record_error()` are the only way to write them —
each call always creates a brand-new entry.

## Permissions

- An agent always has full (read + write) access to memory it owns.
- Shared (`owner_agent_id=None`) memory is **open by default: readable
  and writable by anyone**, with no grant required. This is deliberate —
  the basic "just save something" use case shouldn't need permission
  setup, and an early draft of this design required a grant for shared
  writes too, which meant nearly every straightforward `save()` call
  failed until the caller had pre-configured one. Caught during
  verification, not left in.
- What actually requires an explicit `MemoryPermissionGrant` (via
  `grant_permission(agent_id, owner_agent_id=..., can_read=..., can_write=...)`)
  is reading or writing **another specific agent's private memory** —
  that's the one case where "memory permissions per agent" has real
  teeth, and it's fully enforced and tested.
- A denied `get()`/`save()`/`delete()` **raises**
  `MemoryPermissionDeniedError`. A denied entry inside `query()` is
  **silently excluded** instead — a bulk read shouldn't raise once per
  entry it can't see.

## Obsidian vault integration: what's real vs. placeholder

`markdown.py`'s `entry_to_markdown()` is real, tested code: it renders a
`MemoryEntry` as an Obsidian note — YAML frontmatter (id, scope, tags,
owner) followed by the structured value and, if backlinks exist, a
`## Backlinks` section using `[[wiki-link]]` syntax, matching Obsidian's
own linking convention. What's **not** implemented is
`ObsidianVaultAdapter` actually touching a filesystem — every method
raises `NotImplementedError`, consistent with every other adapter in
this codebase (Tool Manager's OpenAI/Claude/... adapters) and with "do
not connect to live external APIs yet," which this project treats as
covering local vault I/O too, not just network calls.

A `MemoryBackend` (like `ObsidianVaultAdapter`) is always **best-effort**:
a failed sync is caught, published as `memory_manager.backend.sync_failed`,
and never fails the `save()`/`delete()` that triggered it. The in-process
store is the source of truth; the backend is a mirror.

## Future vector search

`VectorSearchProvider` (contracts.py) declares `embed()` and `search()`.
`search_similar()` delegates to it and raises
`VectorSearchNotConfiguredError` if none was given — it never silently
returns an empty list for a capability that isn't wired up.
`NullVectorSearchProvider` (adapters/null_vector_search.py) is a
placeholder proving the shape; nothing computes a real embedding yet.

## Folder structure

```
hermes/modules/memory_manager/
├── README.md
├── models.py            <- MemoryEntry, MemoryPermissionGrant, MemoryScope
├── contracts.py           <- MemoryBackend, VectorSearchProvider protocols
├── errors.py                <- UnknownMemoryEntryError, MemoryPermissionDeniedError, VectorSearchNotConfiguredError
├── events.py                  <- memory_manager.* event constants
├── markdown.py                   <- entry_to_markdown (real, tested Obsidian rendering)
├── service.py                      <- MemoryManager itself
├── interface.py                      <- public entry point (build_memory_manager)
├── adapters/
│   ├── obsidian.py                       <- ObsidianVaultAdapter (placeholder)
│   └── null_vector_search.py               <- NullVectorSearchProvider (placeholder)
└── tests/
    ├── conftest.py
    ├── test_models.py
    ├── test_markdown.py
    ├── test_adapters.py
    └── test_service.py
```

## What was deliberately not built

No specialist agents, no business workflows (per the brief). No real
Obsidian vault I/O, no real embedding model, no persistence beyond the
in-process dict (consistent with every module so far — the architecture
review already flagged that no module has real persistence yet). No
wiring into Commander's `MemoryResolver` planning contract — that's the
natural next integration step, deliberately not done here, matching how
every other module in this codebase has been built standalone first and
wired in as a separate, explicit task.

## Sprint-2: Cognitive Memory Architecture (Memory Galaxy)

Sprint-2 promoted Memory Manager's six typed cognitive memory
categories from spec into a first-class surface. Per
`Specification/02 - Cognitive Architecture/Memory Galaxy.md`:

| `memory_type`     | Lifetime scope | Default `scope` | Producer |
| ----------------- | -------------- | --------------- | -------- |
| `user_dna`        | long-term      | `persistent`    | Reflection Engine (Phase 6) + Mission System (rare) |
| `working_memory`  | session        | `session`       | Workflow Engine (per turn) |
| `mission_memory`  | per mission    | `persistent`    | Mission System (terminal events) |
| `project_memory`  | per project    | `persistent`    | Reflection Engine |
| `skill_memory`    | long-term      | `persistent`    | Reflection Engine |
| `experience_memory` | per incident | `persistent`    | Reflection Engine |

`Knowledge Graph` and `Reflection Engine` are deliberately NOT
`MemoryType` values:
- **Knowledge Graph** is the structural substrate (the union of
  `tags`, `backlinks`, and the new typed `relationships` field),
  not a separate store.
- **Reflection Engine** is a *process* — it produces entries in
  the four writable destinations above, not in a memory of its
  own.

`Decision History` and `Error History` continue to flow through
the legacy `record_decision` / `record_error` surface (and the
`scope="decision"` / `scope="error"` namespace). They are
historical record categories, not primary cognitive MemoryType
values, so they are NOT added to the `MemoryType` Literal.

### Typed write

```python
await manager.record_typed(
    requesting_agent_id="reflector",
    memory_type="skill_memory",
    key="budget:alert:cost_threshold",
    value={"pattern": "alert when daily cost > $50"},
    confidence=0.92,
    importance=0.7,
    provenance=[Provenance(source_type="log_entry", source_id="...", description="...")],
    tags=["reflection:skill", "reflection_engine:managed"],
)
```

Defaults:
- `scope` resolves from `memory_type` (`session` for
  `working_memory`, `persistent` for the other five). Pass
  `scope=` explicitly to override.
- `tags` are augmented with `memory:<memory_type>` and
  `reflection_engine:managed` automatically.

### Supersession (additive only)

```python
await manager.mark_superseded(
    requesting_agent_id="reflector",
    entry_id=old_entry.id,
    superseded_by=new_entry.id,
)
```

The old entry is never deleted. It gets `superseded_by` set, is
tagged `superseded`, and is hidden from `query()` results unless
the caller passes `include_superseded=True`.

### Knowledge Graph substrate

The Knowledge Graph is the structural substrate of Memory Galaxy.
Three fields compose it:

- `MemoryEntry.tags` — facet-style classification; the legacy
  storage for `reflection:<destination>` and
  `reflection_engine:managed`.
- `MemoryEntry.backlinks` — uuids, the looser untyped reverse edge.
- `MemoryEntry.relationships` — typed, directed edges (Sprint-2
  addition). Substrate for the Knowledge Graph traversal below.

```python
# Graph traversal helpers
outbound = await manager.find_relationships(
    requesting_agent_id="...",
    entry_id=entry_id,
    direction="outbound",  # or "inbound" or "both"
    relationship_type=MemoryRelationshipType.CONFIRMED_BY,  # optional
)
path = await manager.find_path(
    requesting_agent_id="...",
    from_id=entry_a,
    to_id=entry_b,
    max_depth=6,
)
```

`find_path` returns the shortest BFS path through typed edges.
`find_relationships` sorts edges by `weight` descending so the
strongest relationship comes first.

### Migration shim

Sprint-1's Reflection Engine encoded the four destination
memory types via `scope="persistent"` + tags + `value` payload.
The migration shim lifts that to first-class typed fields:

```python
await migrate_memory_galaxy(memory_manager)
```

Idempotent — safe to call repeatedly. Returns the count of
entries lifted. The legacy tag encoding is preserved alongside,
so tag-filter queries continue to work.

### Backwards compatibility

Every existing method (`save`, `get`, `get_by_key`, `query`,
`delete`, `record_decision`, `record_error`, `get_backlinks`,
`get_decision_history`, `get_error_history`, `search_similar`,
`sweep_expired`, `grant_permission`, `revoke_permission`) keeps
its signature. The 48 existing tests continue to pass unchanged.

New optional kwargs on `save(...)` (`memory_type`,
`confidence`, `importance`, `provenance`, `superseded_by`,
`relationships`) let callers do typed writes through either
surface. `record_typed(...)` is the documented typed-write
shape; `save(...)` is the lower-level entry point.

### Events

Three new events join the existing five:

- `memory_manager.entry.typed_recorded` — fires after every
  `record_typed` commit (so subscribers can route typed writes
  separately from generic `save` writes).
- `memory_manager.entry.superseded` — fires on every successful
  `mark_superseded` (idempotent re-applications are silenced).
- `memory_manager.migration.completed` — fires once per
  `migrate_memory_galaxy` invocation, regardless of how many
  entries were lifted.

### Folder structure (Sprint-2 additions)

```
hermes/modules/memory_manager/
├── README.md
├── models.py             <- MemoryEntry (typed fields added), MemoryPermissionGrant, MemoryScope
├── typed.py              <- MemoryType Literal, Provenance, MemoryRelationship, GraphPath, MemoryRelationshipType constants
├── migration.py          <- migrate_memory_galaxy (one-shot, idempotent)
├── contracts.py          <- unchanged
├── errors.py             <- unchanged
├── events.py             <- 3 new event constants added
├── markdown.py           <- unchanged (renders typed fields when present)
├── service.py            <- record_typed, mark_superseded, find_relationships, find_path added
├── interface.py          <- re-exports the new typed symbols
├── adapters/             <- unchanged
└── tests/
    ├── conftest.py
    ├── test_models.py
    ├── test_markdown.py
    ├── test_adapters.py
    ├── test_service.py
    └── test_typed.py     <- new: 48 typed-layer tests
```
