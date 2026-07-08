# Hermes Task Queue

Durable execution, retries, crash recovery, and mission/workflow
continuity for dispatched work. This is the module every prior
architecture review named as the biggest open risk: until now, every
piece of this system ran entirely in-process with zero persistence — a
crash mid-request lost everything, silently.

## The two correctness guarantees this module exists to uphold

Commander's `_dispatch_and_await` (core/commander/service.py, unmodified)
matches a task's completion by `str(task.id)` — the id Commander itself
generated for its `DispatchedTask`. For a real queue to satisfy
Commander's `TaskDispatcher` protocol at all:

1. **Identity must round-trip.** `TaskQueueDispatcher` enqueues using
   `id=task.id`, never a fresh one — see `TaskQueue.enqueue`'s `id`
   parameter.
2. **Re-dispatch must be a no-op.** Commander's own task-level retry can
   call `dispatcher.dispatch(task)` again for the *identical*
   `DispatchedTask` (same id) if a completion event doesn't arrive in
   time. `enqueue()` checks for an existing task with that `id` first and
   returns it unchanged — otherwise a retry-driven re-dispatch would
   silently reset an already-in-flight task's state. Proven in
   `test_commander_dispatcher.py`'s
   `test_re_dispatching_the_same_task_does_not_reset_its_state`.

## Two dispatchers now exist for Commander — both stay valid

| Dispatcher | Lives in | Behavior |
|---|---|---|
| `WorkflowEngineTaskDispatcher` | `workflow_engine/commander_bridge.py` (untouched) | Executes inline, synchronously, the instant Commander dispatches. No durability. |
| `TaskQueueDispatcher` | This module | Only enqueues — durably, with retry/priority/scheduling/idempotency — and returns immediately. A `Worker` executes it later and reports back. |

Nothing about the first was removed or changed. This module adds the
second as a genuine alternative for callers that need durability, per
"preserve current architecture" and "do not modify unrelated modules
unless absolutely necessary."

## Architecture

```
 Commander ──dispatch()──▶ TaskQueueDispatcher ──enqueue()──▶  TaskQueue
     ▲                                                          │  (backend: TaskStorageBackend,
     │ task.completed / task.failed                             │   in-memory only for now)
     │                                                          ▼
     └──────────────── complete()/fail() ──────────── Worker ──claim_next()
                                                          │
                                                          ▼ execute()
                                                    TaskExecutor (Protocol)
                                                          │
                                                          ▼
                                          WorkflowEngineTaskExecutor (one concrete impl)
                                                          │
                                                          ▼
                                                    WorkflowEngine.start_run()
```

`Worker` optionally reports `busy`/`idle` heartbeats to State Manager —
the one integration point with it — giving a worker's liveness the same
visibility every other module already has.

## The one change outside this module

Mission System's `execute_mission()` now sets each dispatched request's
`correlation_id` to `mission.id` instead of a fresh random one — the only
existing channel that could carry a mission's identity through
Commander's `Plan` into a `DispatchedTask` (which has no `mission_id`
field of its own). `TaskQueueDispatcher` relies on exactly this
convention to populate `QueuedTask.mission_id`. This is additive; no
existing Mission System behavior changed. See that module's own README
for the mirrored note.

## Requirement → mechanism

| # | Requirement | Mechanism |
|---|---|---|
| 1–2 | Create / persist tasks | `enqueue()`, `TaskStorageBackend` (in-memory now, a future SQLite/Postgres backend satisfies the same Protocol) |
| 3 | Update status | `claim_next()` / `complete()` / `fail()` transitions |
| 4 | Retry failed tasks | `fail()` applies the task's own `RetryPolicy` — the **sixth** reuse of that one primitive (task retry, module restart, tool-call retry, state recovery, workflow steps, now task queue) |
| 5 | Schedule future tasks | `scheduled_for`, checked in `claim_next()` |
| 6 | Task dependencies | `depends_on`; a dead-lettered dependency cascades the failure rather than blocking forever |
| 7 | Priorities | `priority` (lower claimed first), same convention as Capability Registry/Supervisor |
| 8 | Worker assignment | `claim_next(worker_id)` sets `claimed_by`/`claimed_at` |
| 9 | Idempotency keys | `enqueue(idempotency_key=...)` returns the existing task if one already exists for that key — and, separately, for an explicit `id` too (see the two correctness guarantees above) |
| 10 | Dead-letter queue | `list_dead_letter_tasks()`; reached when `RetryPolicy.should_retry` says no |
| 11 | Crash recovery | `recover_expired_claims()` — a claimed task whose visibility timeout passed is presumed to belong to a dead worker, requeued up to `max_claim_attempts`, then dead-lettered |
| 12 | Event publishing | `task_queue.*` events, plus the exact `task.completed`/`task.failed` strings Commander listens for |
| 13 | Mission-level tracking | `QueuedTask.mission_id` + `list_tasks_for_mission()` |
| 14 | Workflow-level tracking | `QueuedTask.workflow_run_id`, set **retroactively** by `WorkflowEngineTaskExecutor` once a `WorkflowRun` exists (Workflow Engine's own internals are untouched, so this id genuinely isn't known until after `start_run()` begins) + `list_tasks_for_workflow_run()` |

## Folder structure

```
hermes/modules/task_queue/
├── README.md
├── models.py                 QueuedTask, TaskExecutionResult
├── contracts.py                TaskStorageBackend, TaskExecutor, HeartbeatReporter protocols
├── errors.py                     UnknownTaskError, InvalidTaskStateError
├── events.py                       task_queue.* events + the shared task.completed/task.failed strings
├── backends.py                       InMemoryTaskBackend (the only TaskStorageBackend so far)
├── service.py                          TaskQueue itself
├── worker.py                             Worker (claim/execute loop, optional State Manager heartbeats)
├── commander_dispatcher.py                 TaskQueueDispatcher (the Commander seam)
├── workflow_executor.py                      WorkflowEngineTaskExecutor (the Workflow Engine seam)
├── interface.py                                build_task_queue, build_worker
└── tests/
    ├── conftest.py, fakes.py
    ├── test_models.py, test_backends.py
    ├── test_service.py                              (the bulk: all 14 requirements)
    ├── test_worker.py
    ├── test_commander_dispatcher.py                    (the identity/idempotency guarantees)
    └── test_integration.py                                (real Commander+Queue+Worker+Workflow Engine+Mission System+State Manager+Event Bus)
```

## What is real vs. placeholder

**Real**: every requirement in the table above, backed by working code
and tests, including a full run through the actual durable path (enqueue
→ claim → execute a real workflow → complete → Commander sees it) in
`test_integration.py` — not a synchronous inline shortcut.

**Placeholder / explicitly out of scope**: no database backend exists
yet — `InMemoryTaskBackend` is the only `TaskStorageBackend`
implementation, per "do not add database dependencies yet." No external
API is called anywhere. No specialist agents or business workflows exist
in this module.

## Running the tests

```
cd ~/hermes-os && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest hermes/modules/task_queue
```

Same standing caveat as every module in this codebase: pytest has not
actually been installed/run in this environment — only ad hoc
verification has.
