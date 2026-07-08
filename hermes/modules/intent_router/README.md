# Hermes Intent Router

A generic, provider-independent request router. It implements Commander's
own `IntentClassifier` and `WorkflowResolver` Protocols
(`core/commander/contracts.py`, unmodified) from one shared routing
table, so a single `IntentRouter` instance satisfies both collaborators
when wiring a real Commander — replacing the fixed-response test fakes
every demo used before this.

## Why this had to exist

The first vertical slice's `WorkflowResolver` always picked the same
workflow, regardless of what the request said. That's fine for proving
the pipe works once, but it isn't "real intent routing" — a router that
can't say no isn't discriminating between anything.

## Matching order

Three passes, checked in this exact order, each stronger signal beating
every weaker one **regardless of route priority**:

1. **Explicit intent hint** — `request.metadata["intent"]` matched
   against a route's `intent_names`. Confidence 1.0. This is the only
   way to route request text that wouldn't otherwise match any keyword
   or command — e.g. a purpose-built CLI that already knows exactly
   which workflow it wants (see the Research Brief demo's own CLI).
2. **Leading command token** — `raw_input.startswith(route.command)`.
   Confidence 0.9.
3. **Keyword substring**, case-insensitive. Confidence 0.6.

`priority` only breaks ties **within** one pass (e.g. two routes that
both declare a matching command) — it can never let a low-priority
keyword route beat a higher-confidence command match on some other
route. A request matching nothing becomes `Intent(name="unknown",
confidence=0.0)`; resolving it raises `UnknownIntentError` unless a
`default_workflow_id` was configured. That failure path is what proves
the router genuinely discriminates rather than being a fixed pass-through
wearing a routing-shaped costume.

## No provider, no model call

Every match is a plain string comparison — `in`, `startswith`,
case-folded substring search. Nothing here calls an LLM, a classifier
service, or any external API. That's the "provider-independent"
requirement: this router works identically whether or not any real
model integration ever exists.

## Usage

```python
router = build_intent_router()  # or default_workflow_id=... for a fallback
router.add_route(WorkflowRoute(
    workflow_id="research_brief",
    intent_names=["research_brief"],
    keywords=["research", "investigate", "brief"],
    command="/research",
))

commander = build_commander(
    intent_classifier=router,
    workflow_resolver=router,   # the SAME instance satisfies both protocols
    ...,
)
```

## Folder structure

```
hermes/modules/intent_router/
├── README.md
├── models.py       <- WorkflowRoute
├── errors.py         <- UnknownIntentError
├── service.py          <- IntentRouter
├── interface.py          <- build_intent_router
└── tests/
    ├── test_models.py
    └── test_service.py
```

## What this does not do

No NLU, no embeddings, no fuzzy matching, no learning from past
requests — those are all legitimate future upgrades to the SAME
`IntentClassifier`/`WorkflowResolver` protocol surface, not something
this task built. This is deliberately the simplest thing that can
genuinely discriminate between requests using structured signals.
