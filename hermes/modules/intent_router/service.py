"""IntentRouter -- a generic, provider-independent request router.

Implements BOTH of Commander's own Protocols (`IntentClassifier` and
`WorkflowResolver`, core/commander/contracts.py -- unmodified) from one
shared routing table, so a single `IntentRouter` instance can be passed
as both collaborators when wiring a Commander. Matching is pure string
comparison -- no model call, no external API -- checked in three passes,
each stronger signal beating any weaker one regardless of a route's own
`priority`:

1. Explicit intent hint (`request.metadata["intent"]`) -- confidence 1.0.
   The caller already knows what it wants (e.g. a purpose-built CLI);
   this is the only way to route request text that would otherwise
   match no keyword or command at all.
2. A leading command token (`request.raw_input.startswith(route.command)`)
   -- confidence 0.9.
3. A keyword substring, case-insensitive -- confidence 0.6.

`priority` only breaks ties WITHIN one of these passes (e.g. two routes
that both declare a matching command). A request matching nothing
becomes `Intent(name="unknown", confidence=0.0)`; resolving that intent
raises `UnknownIntentError` unless a `default_workflow_id` was
configured -- this is what makes the router genuinely discriminating
rather than a fixed pass-through to one workflow.
"""
from __future__ import annotations

from hermes.core.commander.models import IncomingRequest, Intent, WorkflowPlan
from hermes.modules.intent_router.errors import UnknownIntentError
from hermes.modules.intent_router.models import WorkflowRoute

_UNKNOWN_INTENT = "unknown"


class IntentRouter:
    def __init__(self, *, default_workflow_id: str | None = None) -> None:
        self._routes: list[WorkflowRoute] = []
        self._default_workflow_id = default_workflow_id

    def add_route(self, route: WorkflowRoute) -> None:
        self._routes.append(route)
        self._routes.sort(key=lambda r: r.priority)

    async def classify(self, request: IncomingRequest) -> Intent:
        match = self._match(request)
        if match is not None:
            workflow_id, confidence = match
            return Intent(name=workflow_id, confidence=confidence, slots={"topic": request.raw_input})
        if self._default_workflow_id is not None:
            return Intent(name=self._default_workflow_id, confidence=0.0, slots={"topic": request.raw_input})
        return Intent(name=_UNKNOWN_INTENT, confidence=0.0, slots={"topic": request.raw_input})

    async def resolve(self, intent: Intent, request: IncomingRequest) -> WorkflowPlan:
        if intent.name == _UNKNOWN_INTENT:
            raise UnknownIntentError(request.raw_input)
        return WorkflowPlan(workflow_id=intent.name, name=intent.name, steps=[])

    def _match(self, request: IncomingRequest) -> tuple[str, float] | None:
        explicit_intent = request.metadata.get("intent")
        if explicit_intent:
            for route in self._routes:
                if explicit_intent in route.intent_names:
                    return route.workflow_id, 1.0

        text = request.raw_input.strip()
        for route in self._routes:
            if route.command and text.startswith(route.command):
                return route.workflow_id, 0.9

        lowered = text.lower()
        for route in self._routes:
            if route.keywords and any(keyword.lower() in lowered for keyword in route.keywords):
                return route.workflow_id, 0.6

        return None
