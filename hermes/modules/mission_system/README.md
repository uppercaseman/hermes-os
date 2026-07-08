# Hermes Mission System

Converts a user **goal** into an executable **mission**: success
criteria, required capabilities/tools/memory/workflows/approvals, a
temporary specialist **team**, a status, and final outputs.

## Where it sits

```
   user goal                                    "handle this one request"
       │                                                    ▲
       ▼                                                    │
┌─────────────────┐   creates/dissolves    ┌───────────────────┐
│  MissionSystem    │◄──────────────────────│    TeamBuilder      │
│  (this module)     │   temporary roles      │  (this module)       │
└─────────┬─────────┘                        └───────────────────┘
          │ handle_request() per required workflow          │ grant/revoke
          ▼                                                  ▼
┌─────────────────┐        ┌─────────────────┐    ┌───────────────────┐
│    Commander      │───────▶│  Workflow Engine  │    │   Memory Manager    │
└─────────────────┘        └─────────────────┘    └───────────────────┘
          ▲
          │ classify() / resolve()
┌─────────────────┐
│  Intent Router     │
└─────────────────┘
```

Commander answers "handle this one request." A Mission answers
"accomplish this goal" — which may run **one or more workflows**, in
sequence, over its lifetime, entirely by calling `Commander.handle_request()`
once per required workflow. Mission System dispatches nothing directly;
it has no task, no step, no retry logic of its own. Execution is 100%
delegated to the already-built Commander → Workflow Engine pipeline.

**Updated for Task Queue integration**: `execute_mission()` sets each
dispatched request's `correlation_id` to `mission.id` rather than a
fresh random one. Commander's own code already carries `correlation_id`
unchanged from a request through `Plan` into `DispatchedTask` — this is
the *only* channel that exists to carry a mission's identity that far,
since `DispatchedTask` has no `mission_id` field of its own. Task
Queue's Commander bridge (`task_queue/commander_dispatcher.py`) relies on
this exact convention for mission-level task tracking. This was the one
change made to this module outside its own task, and it's additive —
existing behavior (one request per required workflow, in order) is
unchanged.

## Three approval tiers, not three implementations of the same thing

| Gate | Scope | Owned by |
|---|---|---|
| Plan-level | Before ANY dispatch for one request | Commander (`resume_after_approval`) |
| Step-level | Between two steps, mid-workflow-run | Workflow Engine (`approval`-kind steps) |
| Mission-level | Before ANY workflow in the mission starts | **This module** (`required_approvals` / `approve()`) |

Each answers a different question at a different point in the
lifecycle. A mission's approval gate exists because "should we commit
this TEAM and this GOAL to executing at all" is a coarser, earlier
question than "should THIS plan dispatch" or "should THIS step proceed" —
neither of the other two gates could stand in for it.

## The Team Builder: temporary roles, not specialist agents

`TeamBuilder.build_team()` turns a mission's `required_capabilities`
(or an explicit `requested_roles` override) into a list of
`SpecialistRole` — a scoped **permission record**, never an agent, never
a model call. Six example role templates ship by default (Research
Specialist, Developer, Reviewer, Architect, Content Writer, QA);
`register_template()` adds more. See `roles.py`'s docstring for why only
Research Specialist and Developer are auto-inferred from capabilities —
the other four don't have a capability in the current vocabulary that's
uniquely theirs, so they're explicit-request-only (`Mission.requested_roles`).

### Memory access is genuinely enforced; tool access is declared

Each role gets a unique `agent_id` (`mission:{mission_id}:{role_name}`)
and a REAL grant via Memory Manager's already-built
`grant_permission()` to the mission's shared memory pool
(`owner_agent_id = str(mission.id)`) — on top of the ownership-based
access Memory Manager already gives every agent to its own private
memory. This is proven in `tests/test_integration.py`: the same
`agent_id` that could read/write the shared pool during the mission is
denied (`MemoryPermissionDeniedError`) the instant `dissolve_team()`
revokes it.

Tool access (`SpecialistRole.allowed_tools`, checked via `can_use_tool()`)
is **declarative only** — Tool Manager has no agent-scoped enforcement
point to hook into (it's a shared registry invoked by tool name, with no
concept of "which caller"). A future real agent implementation would
consult `can_use_tool()`/`can_use_capability()` before invoking Tool
Manager; this framework doesn't enforce it itself, because there is
nowhere in Tool Manager yet for that enforcement to live.

## Mission status lifecycle

```
draft ──assign_team()──▶ team_assigned ──execute_mission()──▶ awaiting_approval
                                │                                    │ approve() all gates
                                │                                    ▼
                                └──────────────────────────────▶ active ──▶ completed / failed
                                                                              │
                                                                              ▼ (any status)
                                                                         dissolve_mission()
                                                                              │
                                                                              ▼
                                                                          dissolved
```

`dissolve_mission()` is deliberately a separate, explicit final step —
never automatic on completion — so a caller can inspect a mission's
outputs before its team's shared-memory access is torn down.

### ADR-0017 reconciliation (Sprint 0)

The diagram above shows the **seven** state values the runtime writes today.
[[ADR/0014 - Adopt the Canonical Mission Lifecycle|ADR 0014]] and the
[[Specification/01 - Architecture/Mission Lifecycle|Mission Lifecycle spec]] name
a canonical **thirteen**-state machine: `created`, `planned`,
`awaiting_approval`, `ready`, `running`, `paused`, `waiting`, `blocked`,
`completed`, `failed`, `cancelled`, `dissolved`, `archived`. Per
[[ADR/0017|ADR 0017]] (Sprint 0), the `MissionStatus` Literal has been
**expanded** to accept all thirteen canonical values **plus** the three
implementation-nicknamed values the runtime writes today (`draft`,
`team_assigned`, `active`). Existing code is unaffected; new code is free
to use the canonical vocabulary. The alias map:

| Runtime value (today) | Canonical equivalent | Notes |
|---|---|---|
| `draft` | `created` | the entry state, before any team exists |
| `team_assigned` | `planned` (with team built) | implementation-named; no canonical equivalent |
| `active` | `running` | post-execution-start |
| `awaiting_approval` | `awaiting_approval` | already canonical |
| `completed` | `completed` | already canonical |
| `failed` | `failed` | already canonical |
| `dissolved` | `dissolved` | already canonical |

The six canonical states the runtime does **not** yet write
(`planned`, `ready`, `running`, `paused`, `waiting`, `blocked`,
`cancelled`, `archived`) are reserved for future work: a future
Sprint-1+ task can migrate the runtime to use them without changing the
type. Validation in `tests/test_models.py` proves both directions of
this contract (every canonical value is accepted; unknown values are
rejected at construction time).

## Success criteria are never auto-evaluated

`SuccessCriterion.met` starts `None` and is only ever set by
`mark_success_criterion()`, called explicitly by a human or a future
system. Automatically judging whether free-text success criteria were
met would need real semantic judgment (an LLM) — out of scope per "do
not connect to any AI APIs," so this framework tracks the *bookkeeping*
of judgment, not the judgment itself.

## Workflow inference via Intent Router

If `Mission.required_workflows` is empty when `execute_mission()` runs,
Mission System calls the configured `IntentResolver` (`classify()` +
`resolve()` — the exact same two methods Commander itself calls) against
the mission's `goal` text, exactly like the Research Brief vertical
slice's CLI does against its own topic. An unroutable goal, or no
`IntentRouter` configured at all, fails the mission (`status="failed"`)
rather than raising — see `errors.py`'s `MissionSystemConfigError`
docstring for exactly where the raise/fail line is drawn, and why it's
principled rather than arbitrary.

## Folder structure

```
hermes/modules/mission_system/
├── README.md
├── models.py            <- Mission, SpecialistRole, SuccessCriterion, ApprovalRecord
├── contracts.py           <- RequestHandler, IntentResolver, MemoryPermissionGranter protocols
├── errors.py                <- UnknownMissionError, MissionNotReadyError, UnknownApprovalGateError, UnknownRoleTemplateError, MissionSystemConfigError
├── events.py                   <- mission_system.* event constants
├── roles.py                      <- RoleTemplate, DEFAULT_ROLE_TEMPLATES (the six example roles)
├── team_builder.py                  <- TeamBuilder
├── service.py                         <- MissionSystem
├── interface.py                         <- build_mission_system, build_team_builder
└── tests/
    ├── conftest.py, fakes.py
    ├── test_models.py, test_roles.py
    ├── test_team_builder.py
    ├── test_service.py
    └── test_integration.py                  <- proves every required integration end to end
```

## What is real vs. framework-only

**Real**: the full mission lifecycle state machine, approval-gate
tracking, capability-to-role inference, and — the one piece with actual
enforcement teeth — shared-memory permission grants/revocations via
Memory Manager. Execution genuinely runs through a real Commander and a
real Workflow Engine in `tests/test_integration.py`.

**Framework-only, by design**: no specialist agents exist anywhere (per
the brief); `SpecialistRole` is data a future agent would consult, not
code that acts. No AI API is called anywhere — success-criteria judgment
and tool-access enforcement are both left as explicit hooks for a future
system, not faked here.
