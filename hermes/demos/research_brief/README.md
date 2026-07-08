# Research Brief — the first Hermes vertical slice

Proof that a user request flows through Commander, a real Workflow
Engine workflow, a real Tool Manager adapter, real Memory Manager
reads/writes, and the real Event Bus, and comes back as a structured
result — with no live external API involved anywhere.

## Run it

```
cd ~/hermes-os
python3 -m hermes.demos.research_brief.cli "the history of the printing press"
```

Prints a JSON structured brief: `status`, `topic`, `summary`, `sources`,
`memory_entry_id`, and `step_statuses` (per-step outcome for all five
workflow steps).

## Run the tests

```
cd ~/hermes-os && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest hermes/demos/research_brief
```

(Same standing caveat as every other module: pytest has not actually
been installed/run in this environment — only ad hoc verification has.)

## The one real design problem this task raised

Commander's `Plan.build_tasks()` and `DispatchedTask` (both unmodified)
only carry `workflow_id`, `agents`, `tools`, and `memory` into a
dispatched task — never the original free-text request. So by the time
the workflow-engine bridge receives the task, the research topic the
user typed is nowhere in it.

The fix doesn't touch either of those already-built files.
`IncomingRequest.correlation_id`, once set explicitly by the caller, is
guaranteed by Commander's own existing code to survive unchanged into
`DispatchedTask.correlation_id`. So `runner.py` keeps a small
correlation-id-keyed registry: a demo-only wrapper around the real
`IntentRouter` (the first collaborator to ever see the raw request text)
stashes the topic there, and a demo-only dispatcher
(`_TopicInjectingDispatcher`) looks it up and injects it into the task's
payload immediately before **delegating to the real, unmodified
`WorkflowEngineTaskDispatcher`** — reusing its dispatch/report logic
rather than duplicating it.

## Real intent routing (no longer fixed)

The original vertical slice's `WorkflowResolver` always returned the
same workflow, regardless of the request. It now uses a real
`IntentRouter` (`modules/intent_router`) with one registered route for
`research_brief` (matching by explicit intent, the `/research` command,
or the keywords `research`/`investigate`/`brief`). The CLI still always
reaches Research Brief for any topic text — not because the router can't
discriminate, but because `run_research_brief` sets
`metadata={"intent": "research_brief"}` on the request, the router's
explicit-intent-hint match. That's the *correct* way for a purpose-built,
single-workflow CLI to route (free-form research topics can't be relied
on to contain any particular keyword). The router's actual
discrimination — command/keyword matching without that metadata, and
genuinely failing on unmatched input rather than running Research Brief
anyway — is exercised directly in `tests/test_runner.py` by calling
Commander without the shortcut.

## What each of the five steps actually does

| # | Step | Kind | What it proves |
|---|---|---|---|
| 1 | `accept_topic` | `noop` | Structural marker — see below for why it can't carry data |
| 2 | `read_memory` | `memory_read` | Real Memory Manager read, topic-templated key |
| 3 | `call_research_tool` | `tool_call` | Real Tool Manager `invoke()` against the mock adapter |
| 4 | `save_to_memory` | `memory_write` | Real Memory Manager write, templated from step 3's output |
| 5 | `assemble_brief` | `noop` | Structural marker — see below |

Steps 1 and 5 are `noop` because the Workflow Engine's `noop` kind
always returns `{}`, so it has nothing to hand downstream. Steps 2–4
reference `{{input.topic}}` directly rather than a prior noop's output.
"Return a structured brief" (step 5) is genuinely satisfied — just by
`runner.assemble_brief()` reading the run's `step_results` after the
fact, not by a DAG node producing it.

## Template-resolved memory keys (no longer one fixed slot)

`memory_key` on steps 2 and 4 is now `"research_brief/{{input.topic}}"`,
not a fixed string — the Workflow Engine template-resolves `memory_key`
the same way it already resolved `parameters`/`memory_value_template`
(see `workflow_engine/service.py`'s `_resolve_memory_key`, added for
this fix). Two different topics now resolve to two different keys and
never collide (`test_different_topics_do_not_share_a_memory_entry`);
the same topic run twice still resolves to the same key, so
within-topic accumulation still works exactly as before
(`test_repeating_the_same_topic_shares_the_same_memory_entry`).

## Files

```
hermes/demos/research_brief/
├── README.md
├── mock_research_adapter.py   <- the one fake tool adapter (permanently mock, unlike Tool Manager's placeholders)
├── workflow.py                  <- the 5-step WorkflowDefinition
├── runner.py                      <- pipeline wiring + the topic-carrying fix + assemble_brief
├── cli.py                           <- `python3 -m hermes.demos.research_brief.cli "<topic>"`
└── tests/
    ├── test_mock_research_adapter.py
    ├── test_workflow.py
    ├── test_runner.py               <- the actual vertical-slice proof
    └── test_cli.py
```

## What's real vs. mocked

**Real**: Commander's full planning/dispatch/retry pipeline, the
Workflow Engine's DAG scheduler and step execution (including retries/
timeouts as configured on `call_research_tool`), Tool Manager's
retry/rate-limit/invoke plumbing, Memory Manager's permissioned
save/get_by_key, and every event published along the way.

**Mocked**: only `MockResearchAdapter`'s content — a canned summary and
two `example.invalid` source URLs (a reserved, non-resolving TLD, so
it's unambiguous these were never meant to hit anything real).

## What should be built next

- **A real Tool Manager adapter** replacing one of the six placeholders,
  now that a full path from Commander to a real external call exists to
  plug it into.
- **Wiring Capability Registry into Commander's own `ToolResolver`**, so
  Commander's planning-level tool bookkeeping (currently a no-op in this
  demo) reflects what Workflow Engine will actually use.
- **A second registered workflow** to make the Intent Router's
  discrimination visible in this demo itself, not just in its test
  suite (right now there's still only one workflow to route to).
