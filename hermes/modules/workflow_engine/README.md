# Hermes Workflow Engine

Turns a registered workflow definition into a running, multi-step
process: sequencing, conditional branching, parallel steps, retries,
timeouts, human approval gates, tool calls via Tool Manager, and memory
reads/writes via Memory Manager.

## The one architectural decision this task was actually about

Commander already plans requests and dispatches tasks. Workflow Engine
executes steps. The risk this task named explicitly — "do not duplicate
Commander responsibilities" — comes down to one question: **who decides
what a "task" is when a workflow has ten steps, branches, and a parallel
section?**

Commander's own code already answers this, unmodified. `Plan.build_tasks()`
(`core/commander/models.py`) has always contained this fallback:

```python
steps = self.workflow.steps or [self.workflow.name]
```

If a `WorkflowPlan.steps` list is empty, Commander dispatches **exactly
one** task, keyed by the workflow's `name` — not one per step. That is
the seam. A `WorkflowResolver` that wants Workflow Engine to own
execution returns `WorkflowPlan(steps=[], name=<workflow_id>, ...)`;
Commander then hands off ONE opaque task instead of fanning out over a
flat step list the way it does today for anything else. Nothing in
Commander changed to make this true — the docstring on `build_tasks()`
already said as much: "dependency-aware execution belongs to the future
Workflow Engine module."

`commander_bridge.py`'s `WorkflowEngineTaskDispatcher` is what receives
that one task: it satisfies Commander's existing `TaskDispatcher`
protocol, reads the task's `payload["step"]` as a workflow_id, runs the
**entire** workflow — sequencing, branching, parallelism, retries,
approval gates, all of it — and reports back with `task.completed` /
`task.failed` on the same event bus Commander's own
`_dispatch_and_await` already listens to. Commander never sees a step. It
sees one task, dispatched once, that eventually completes or fails.

This is proven, not just described: see `tests/test_commander_bridge.py`,
which wires a real Commander to a real `WorkflowEngine` and asserts
Commander dispatches exactly one task regardless of how many internal
steps the workflow has (even a 10-step workflow).

### Two approval gates, two different scopes

Commander's `resume_after_approval` gates an entire plan **before any
dispatch happens at all**. This engine's `approval`-kind steps gate one
step **inside an already-dispatched, in-flight run**, between two other
steps. These aren't the same mechanism reimplemented twice — they answer
different questions at different points in the lifecycle, and neither
could stand in for the other: Commander's gate can't pause mid-workflow,
and a step-level gate has no concept of "should this request have been
accepted at all."

## Scheduling model: one mechanism, not two

Step sequencing (#2) and parallel steps (#4) are the same mechanism seen
from two angles, not two separate features. Every step declares
`depends_on`. The scheduler runs in "waves": every step whose
dependencies are all in a terminal state (`completed`/`failed`/`skipped`)
and hasn't started yet runs **concurrently** via `asyncio.gather`, then
the next wave is computed. A chain of single dependencies is the
degenerate sequential case; several steps with no dependency on each
other is the parallel case. No separate "parallel group" construct
exists because none is needed.

## Conditional branching, including failure-handling branches

A `StepCondition` inspects a prior step's status or a dotted path into
its output — no `eval`/`exec`, ever. It doubles as a failure-handling
mechanism: a step whose dependency **failed** is normally skipped, unless
that step's own condition specifically inspects the failed dependency
(e.g. `equals="failed"`) — that's an intentional error-handling branch,
and it's allowed to run precisely because it's designed to react to the
failure, not ignore it. `register_workflow` enforces that a condition's
referenced step is always in that step's own `depends_on`, so the
referenced step is guaranteed to have already run before the condition
is evaluated.

## Retries, timeouts, failure recovery

Every step carries its own `RetryPolicy` (`core/supervisor/policy.py` —
now reused a fifth time across this codebase: task retry, module
restart, tool-call retry, state recovery, and now workflow steps) and
`timeout_seconds`. A tool_call step's failure can come from two layers —
Tool Manager's own internal retries, then this step's retries on top —
which is a deliberate two-tier design matching Supervisor/State
Manager's precedent, not an oversight; set a step's `retry_policy` to
`max_attempts=1` if the underlying tool already retries robustly.

`resume_run()` is failure recovery at the workflow level: it forgets
only the steps that ended `failed` and re-advances, so already-`completed`
steps are never re-run.

## Tool calls / memory / capability resolution

A `tool_call` step names either a `tool_name` directly or a `capability`
(resolved via an optional `CapabilitySelector`, matching "never request a
specific provider") — registration rejects a step with both or neither.
`memory_read`/`memory_write` steps talk to an optional `MemoryStore`.
Both integrations are Protocols (`contracts.py`), not concrete Tool
Manager/Memory Manager classes — a step's parameters, `memory_key`, and
`memory_value_template` can all reference `{{input.<path>}}` or
`{{steps.<name>.output.<path>}}` via a small, safe, regex-based templater
(`templating.py`) with no `eval`. A templated `memory_key` (e.g.
`"research_brief/{{input.topic}}"`) is what lets one generic workflow
definition address a different memory entry per run instead of sharing a
single fixed slot — see `_resolve_memory_key` in service.py.

Every collaborator (`event_bus`, `tool_manager`, `memory_manager`,
`capability_registry`) is optional. A step that needs one it wasn't
given fails clearly with `WorkflowEngineConfigError`, not a crash.

## Status tracking

`get_run`/`get_run_status` are **synchronous**, on purpose — the same
rationale as State Manager's query methods: a pure in-memory read must
never be blocked. `WorkflowRun.step_results` gives per-step status,
attempts, timing, output, and error, not just an overall run status.

## Generic templates only

`templates.py` has three structural builders — `sequential_template`,
`fan_out_fan_in_template`, `approval_gated_template` — parameterized
purely by step names. No business-specific workflow is defined anywhere
in this codebase, per the brief.

## Folder structure

```
hermes/modules/workflow_engine/
├── README.md
├── models.py             <- StepDefinition, StepCondition, WorkflowDefinition, StepResult, WorkflowRun
├── contracts.py            <- ToolInvoker, MemoryStore, CapabilitySelector protocols
├── errors.py                 <- InvalidWorkflowDefinitionError, UnknownWorkflow(Run)Error, WorkflowEngineConfigError
├── events.py                   <- workflow_engine.* event constants
├── templating.py                  <- resolve_templates (pure, no eval)
├── templates.py                     <- generic workflow template builders
├── service.py                         <- WorkflowEngine (validation + scheduler + step execution)
├── interface.py                         <- public entry point (build_workflow_engine)
├── commander_bridge.py                    <- WorkflowEngineTaskDispatcher (the Commander seam)
└── tests/
    ├── conftest.py, fakes.py
    ├── test_models.py, test_templating.py, test_templates.py
    ├── test_service.py
    └── test_commander_bridge.py            <- proves the Commander integration end to end
```

## What works vs. placeholder

Everything above is real, working logic — the scheduler, validation,
retries, timeouts, approval gates, templating, and the Commander bridge
all execute for real. What's placeholder is what it depends on: Tool
Manager's adapters are still `NotImplementedError` stubs, so any
`tool_call` step against a real provider will fail until a real adapter
exists — this engine's job was to be ready for that day, not to build it.
No business-specific workflow, no specialist agent, and no live external
API calls exist anywhere in this module, per the brief.

## Running the tests

```
cd ~/hermes-os && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest hermes/modules/workflow_engine
```

(Same standing caveat as every other module in this codebase: pytest has
not actually been installed/run in this environment yet.)
