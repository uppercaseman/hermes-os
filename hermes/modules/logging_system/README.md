# Hermes Logging System

Captures, stores, queries, and replays system activity. Subscribes to
the Event Bus like any other module -- it has no privileged access, no
hook into other modules' internals, and no ability to change what
happens. It is a listener, a store, and a query layer, nothing else.

## Architecture

```
hermes/modules/logging_system/
  __init__.py
  models.py       LogEntry -- the single unit of persisted, queryable log data
  contracts.py    LogStorageBackend Protocol -- the persistence seam
  errors.py       UnknownLogEntryError
  severity.py     classify_severity() -- infers error/warn from event_type keywords
  redaction.py    default_redactor() -- strips secrets before persistence
  backends.py     InMemoryLogBackend -- the only backend today
  service.py      LoggingSystem -- capture/query/replay/export
  interface.py    build_logging_system() + public re-exports
  tests/
    conftest.py
    test_models.py
    test_severity.py
    test_redaction.py
    test_backends.py
    test_service.py
    test_integration.py
```

### Why a service, not middleware

Every other module publishes events for its own reasons (Commander for
orchestration, Task Queue for durability, State Manager for health).
Logging System adds nothing to that traffic -- it only listens
(`"*"` wildcard subscription), transforms each `Event` into a
`LogEntry` (redact -> classify severity -> derive foreign keys), and
stores it. No module's behavior changes by Logging System being
present or absent, subscribed or not. This is the same non-invasive
posture State Manager took toward health, applied to activity.

### `LogEntry`: one record, computed once

```python
class LogEntry(BaseModel):
    id: uuid.UUID
    event_type: str
    source_module: str
    correlation_id: uuid.UUID
    severity: Severity                    # "debug" | "info" | "warn" | "error"
    payload: dict[str, Any]               # redacted, never the raw event payload
    mission_id: uuid.UUID | None
    workflow_run_id: uuid.UUID | None
    task_id: uuid.UUID | None
    tool_name: str | None
    captured_at: datetime
```

`mission_id` / `workflow_run_id` / `task_id` / `tool_name` are derived
**once, at capture time**, not recomputed per query -- `query()` is a
straight in-memory filter over already-derived fields, not a
per-request payload parse.

### Severity is inferred, not trusted

No module in Hermes actually sets `Event.level` meaningfully today --
every publisher defaults it to `"info"`. `classify_severity()`
(severity.py) infers real severity from `event_type` keyword matching
(`failed`, `dead_letter`, `crashed`, `denied`, `unavailable`,
`exhausted` -> error; `retry`, `unhealthy`, `degraded`, `recovered`,
`skipped` -> warn), while still honoring an explicitly-elevated
`level` if a module ever starts setting one for real -- inference
never downgrades an explicit signal.

### Redaction happens before persistence, not before display

`default_redactor()` (redaction.py) strips two classes of secret:
key-name matches (`api_key`, `secret`, `token`, `password`,
`credential`, `authorization` -- case-insensitive, substring) and
value-pattern matches (`sk-`/`pk-`/`rk-`-prefixed strings, the shape
of a real provider API key). This runs on `capture()`, before the
entry ever reaches the backend -- there is no unredacted copy sitting
anywhere waiting to be queried. This exists because a real OpenAI key
was found sitting in this environment's shell variables during the
Tool Manager work earlier in this build; a logging system that stores
raw event payloads verbatim would have captured it into a log entry
the moment that adapter made its first real call.

### Mission-level tracking: correlation_id, with one honest gap

Mission System sets `correlation_id = mission.id` on every request it
sends to Commander. That value survives, unmodified, through
Commander's `DispatchedTask` and into `TaskQueueDispatcher`, so
Commander's own events and every Task Queue event for that mission's
task share `correlation_id == mission.id`. `list_by_mission()` checks
both `entry.mission_id == mission_id` (an explicit payload field, used
by Mission System's own events) and `entry.correlation_id ==
mission_id` (the propagated convention) -- either one qualifies.

**Workflow Engine does not participate in this chain.** `WorkflowRun`
mints its own fresh `run.id` and every event it publishes is
correlated by that run id, not by whatever correlation_id the
triggering task happened to carry. So `list_by_mission(mission.id)`
will **not** surface Workflow Engine's own events -- this is verified
directly in `test_integration.py`
(`assert not any(e.source_module == "workflow_engine" ...)`). Reaching
workflow-level activity for a mission requires the two-step lookup
Task Queue already supports: `task_queue.list_tasks_for_mission(mission_id)`
to get the task, read its `.workflow_run_id`, then
`logging_system.list_by_workflow_run(workflow_run_id)`. This is a
known, deliberate boundary (see "Known gaps" below), not a bug --
closing it would mean either Task Queue starting to pass mission_id
into Workflow Engine (a new cross-module coupling) or Workflow Engine
inheriting a caller's correlation_id instead of minting its own (which
would break its own internal per-run event correlation). Both were
judged not worth doing without being asked.

### Sync vs. async: this module is async, deliberately

State Manager, Workflow Engine, and Mission System expose **sync**
query methods -- they're plain in-process dicts that must never block
a caller. Task Queue and Logging System expose **async** query
methods, because both are explicitly built against a
`Protocol`-defined storage backend (`LogStorageBackend`,
`TaskPersistenceBackend`) that could plausibly be I/O-bound in a real
deployment. This split is intentional and consistent across both
modules that made this same in-memory-now/pluggable-later choice.

### "Raise on misuse, return empty on absence"

`get_entry(id)` raises `UnknownLogEntryError` -- asking for a specific
entry that doesn't exist is a caller error. Every `query()`/`list_by_*`
method returns `[]` for no matches -- an empty result set is a normal,
expected outcome, not an error condition. Same rule Task Queue and
Mission System already follow.

## Requirement -> mechanism map

| Requirement | Mechanism |
|---|---|
| Subscribe to Event Bus | `LoggingSystem.start()` subscribes `capture()` to `"*"` |
| Structured event logs | `LogEntry` model, one record per captured `Event` |
| Correlation IDs | `LogEntry.correlation_id`, carried straight from `Event.correlation_id` |
| Mission-level logs | `list_by_mission()` -- dual match on `mission_id` field or `correlation_id` |
| Workflow-level logs | `list_by_workflow_run()` -- matches `run_id` in payload |
| Task-level logs | `list_by_task()` -- matches `task_id` in payload |
| Provider/tool logs | `list_by_tool()` -- matches `tool_name` in payload |
| Error logs | `list_errors()` -- `severity == "error"`, inferred by `classify_severity()` |
| Health/status logs | `list_health_logs()` -- `source_module in ("state_manager", "supervisor")` |
| Query by module/mission/workflow/task/severity/timestamp/correlation_id | `query()` -- AND-combines every supplied filter |
| In-memory backend + persistence interface | `InMemoryLogBackend` implements `LogStorageBackend` Protocol |
| Replay support | `replay(correlation_id)` + `render_replay()` for a human-readable timeline |
| Export support | `export()` / `export_json()` -- JSON-serializable, filter-aware |
| Redaction hooks | `redaction_hook` constructor param, defaults to `default_redactor` |

## What's real

- Real Event Bus subscription (`"*"` wildcard) and real `Event` -> `LogEntry` capture.
- Real severity inference, real redaction (both key-name and value-pattern), applied unconditionally on every captured entry.
- Real in-memory storage behind a real `Protocol` seam (`LogStorageBackend`) -- swapping in a database-backed implementation later requires no change to `LoggingSystem` itself.
- Real, filter-combining `query()`; real `replay()`/`render_replay()`; real `export()`/`export_json()`.
- `tests/test_integration.py` wires a real Commander, Mission System, Workflow Engine, Task Queue + Worker, Tool Manager (scripted adapter), State Manager, and one real Event Bus, drives an actual mission to completion, and asserts Logging System captured and correctly queries entries from every one of those modules -- not a mocked approximation of them.

## What's placeholder only

- Persistence is in-memory only, per the task's explicit instruction ("do not add database dependencies yet"). `LogStorageBackend` is the seam a real backend would implement.
- No dashboard/UI consumes `export()`/`export_json()` yet -- they exist as the shape a future one would call.
- No log rotation, retention policy, or size-bounded eviction exists. `InMemoryLogBackend` grows without bound for the lifetime of the process.
- Redaction is best-effort pattern matching, not a guarantee -- a secret that doesn't match a known key name or a known provider key-prefix shape will not be caught.

## Known architectural gaps

1. **Workflow-level activity is not reachable from a mission_id alone** (documented in detail above) -- requires the two-step `task_queue.list_tasks_for_mission()` -> `.workflow_run_id` -> `logging_system.list_by_workflow_run()` path. Closing this would require a cross-module change to either Task Queue or Workflow Engine that wasn't requested.
2. **No log volume controls.** A long-running Hermes process will accumulate every captured event forever in `InMemoryLogBackend`. Fine for the current in-memory-everything phase; will need retention/rotation policy once a real backend exists.
3. **Severity is inferred from `event_type` strings, not asserted by publishers.** If a future module introduces a new failure-shaped event name that doesn't contain any of the current keyword list, it will silently classify as `"info"` until the keyword list is extended.
4. **Redaction is name/shape-based, not schema-based.** A secret placed under an innocuously-named key (e.g. `payload["notes"] = "sk-..."`) would still be redacted by the value-pattern rule, but a secret with neither a matching key name nor a matching value shape would pass through unredacted.

## Safest next module

**Configuration Manager.** Every module built so far (Commander,
Supervisor, Tool Manager, Capability Registry, State Manager, Memory
Manager, Workflow Engine, Intent Router, Mission System, Task Queue,
Logging System) currently hardcodes its own defaults in Python
(retry policies, timeouts, poll intervals, redaction patterns) with no
central place to override them per-environment. A Configuration
Manager is additive only -- it reads what already exists as
constructor defaults and gives them one external home -- and touches
no other module's runtime behavior until something is explicitly
wired to consume it. It also has no dependency on Logging System or
vice versa, so it's a clean, low-risk next step that doesn't require
touching anything just built.
