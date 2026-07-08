# Hermes State Manager

The canonical, Commander-facing record of every module's health and
lifecycle. Every module reports one of seven states:

`healthy`, `busy`, `idle`, `offline`, `restarting`, `failed`, `degraded`

## Why this isn't just the Supervisor again

`core/supervisor` already does health monitoring and automatic restart —
so what does this module add? Two things Supervisor structurally cannot
provide:

1. **Workload, not just liveness.** Supervisor's `health_check()` returns
   a bool. It has no way to know "busy" from "idle" — only a module
   itself knows that. State Manager's **heartbeat** is a *push* channel
   (`report_heartbeat(module_name, state)`) a module uses to report its
   own richer self-assessment, as opposed to Supervisor's *pull*
   (`health_check()` polling).
2. **Cross-module context.** Dependency tracking and system-wide
   diagnostic rollups are entirely new — Supervisor treats every unit
   independently.

Because most modules don't push heartbeats yet, State Manager also
listens to the same Supervisor lifecycle events Tool Manager and the
Capability Registry already consume, so every module is trackable from
day one even before it adopts heartbeat push. See "Active vs
supervisor-derived" below for why that matters.

## Active vs. supervisor-derived tracking

A module is in exactly one of two modes at any time:

- **Passive / supervisor-derived**: its state comes only from translated
  `supervisor.unit.*` events. These are transition-driven (fired only
  when something changes), so a passive module is **exempt** from the
  heartbeat-staleness sweep — no new event just means nothing changed,
  not that the module went silent.
- **Active**: the module has called `report_heartbeat()` at least once.
  From then on, the staleness timeout applies: no heartbeat within
  `heartbeat_timeout_seconds` marks it `offline` and can trigger
  automatic recovery.

This distinction is the one subtle correctness issue this design had to
get right — without it, every currently-existing module (none of which
push heartbeats yet) would get spuriously marked offline the moment the
sweep loop ran, since they'd never have "checked in" on schedule.

## Two-tier automatic recovery

Supervisor already retries a crashing unit fast, with its own bounded
backoff (`core/supervisor/policy.py`'s `RetryPolicy`). State Manager adds
a second, slower tier on top, using the *same* `RetryPolicy` primitive
(now reused a fourth time — task retry, module restart, tool-call retry,
and now this):

- When an **active** module's heartbeat goes stale (implying it's
  alive-but-unresponsive in a way Supervisor's `health_check()` can't
  see), State Manager requests its own restart.
- When Supervisor has **definitively given up** on a unit
  (`unit.restart_exhausted`) or its strategy says never to retry
  (`unit.restart_skipped`), State Manager gets one more (bounded) shot at
  recovery. A plain `unit.crashed`/`unit.restarting` does **not** trigger
  this — Supervisor is already handling that; State Manager only steps in
  once Supervisor's own tier has concluded.

`request_restart()` never raises for the underlying restart failing —
that's reflected in the returned `RestartRequest.status` instead, and
publishes `state_manager.module.restart_failed` if given a Supervisor
that couldn't fulfill it (e.g. the module was never registered there).

## Queries are synchronous, on purpose

`get_state`, `get_state_all`, `diagnostics`, and `diagnostics_all` are
plain synchronous methods — not coroutines. This is a deliberate,
tested guarantee (see `test_query_methods_are_synchronous_by_design`)
that directly satisfies "Commander must be able to query every module at
any time": a sync call cannot be blocked awaiting anything else. Only the
write-side operations (`report_heartbeat`, `request_restart`, `start`,
`stop`) are async, since they publish events and may do real work.

## Dependency tracking

`declare_module(name, depends_on=[...])` records a dependency graph. When
computing a module's **effective** state, if it isn't already in a clear
negative state itself (`failed`/`offline`/`restarting`), and any of its
declared dependencies is `failed` or `offline`, the effective state
becomes `degraded` — even though the module's own last report might say
`healthy`. `diagnostics()` exposes both the raw `reported_state` and the
computed `effective_state` side by side, plus which dependencies are
unmet. Degradation checks each dependency's **raw** reported state, never
its effective state, which is what keeps a cyclic dependency declaration
(A depends on B, B depends on A) from recursing — and is also why
degradation doesn't cascade past one hop.

## Diagnostic reporting / future dashboard support

`diagnostics(name)` and `diagnostics_all()` return plain, JSON-serializable
pydantic models (`ModuleDiagnostics` / `SystemDiagnostics`). That's the
entire "future dashboard support" hook: a future HTTP endpoint serves
`.model_dump()` / `.model_dump_json()` of `diagnostics_all()` directly —
no new serialization layer needed. `SystemDiagnostics.overall_state` is a
simple rollup (`critical` if anything is `failed`, `degraded` if anything
is `degraded`/`offline`/`restarting`, else `healthy`).

## Folder structure

```
hermes/core/state_manager/
├── README.md
├── models.py       <- Heartbeat, RestartRequest, ModuleDiagnostics, SystemDiagnostics
├── errors.py         <- UnknownModuleError
├── events.py           <- state_manager.* event constants
├── service.py            <- StateManager itself
├── interface.py            <- public entry point (build_state_manager)
└── tests/
    ├── conftest.py
    ├── test_models.py
    └── test_service.py
```

## Relationship to Commander

Commander does not yet query the State Manager — that wiring (Commander
calling `get_state`/`diagnostics_all` as part of its own health
reporting, or gating dispatch on a dependency's state) is a natural next
integration step, deliberately not done in this task.
