# Provider Router

> Capability-driven fail-over routing for Hermes OS. The router resolves
> a `capability` (e.g. `reasoning`, `image_generation`) into one or
> more Tool Adapter invocations, walking a ranked fallback chain
> provided by the Capability Registry.

## Why this module exists

Sprint-4 split provider integration into three clean layers:

| Layer | Knows about providers? | Owner |
| --- | --- | --- |
| **Commander** | No. Speaks in `capability` strings. | `hermes/modules/commander/` |
| **Provider Router** (this module) | Knows the *concept* of a provider chain, but not implementation details. | `hermes/modules/provider_router/` |
| **Tool Manager + Adapters** | Yes. Each adapter owns its provider's wire protocol. | `hermes/modules/tool_manager/` |

Commander asks the router for `reasoning`; the router asks the
Capability Registry which providers can satisfy that capability in
priority order, picks the first available one, and dispatches through
Tool Manager. If that provider fails, the router moves to the next
candidate in the chain. Commander never learns which provider was
selected.

## Public surface

```python
from hermes.modules.provider_router import (
    build_provider_router,
    ProviderRouter,
    RoutingRequest,
    ProviderInvocationOutcome,
)

router: ProviderRouter = build_provider_router(
    tool_manager=tool_manager,
    capability_registry=capability_registry,
    event_bus=event_bus,
    failover_max_attempts=3,
)

outcome: ProviderInvocationOutcome = await router.route(
    RoutingRequest(capability="reasoning", parameters={"prompt": "..."})
)
print(outcome.success, outcome.selected_tool_name, outcome.attempts)
```

### `route(request) -> ProviderInvocationOutcome`

The canonical synchronous path. Walks the Capability Registry's
ranked chain for `request.capability`, invokes each candidate through
Tool Manager, returns when one succeeds or the chain is exhausted.

### `route_stream(request) -> AsyncIterator[ToolStreamChunk]`

Streaming path. Resolves to the **top-ranked** provider only -- a
stream that fails mid-way is terminal, so multi-provider fail-over for
streams is deferred. Use `route()` for full fail-over semantics and
dispatch `invoke_stream()` against the resolved `tool_name`
yourself.

## Routing semantics

### Selection

The router calls `capability_registry.resolve_chain(capability)` which
returns a ranked list of `CapabilityCandidate` records. The ranking
combines provider priority, health, latency, cost, supported
capabilities, and policy restrictions -- all evaluated by the
Capability Registry. The router simply trusts the order.

### Fail-over

When `tool_manager.invoke()` returns `status="failed"` (or raises),
the router:

1. Records the attempt in `outcome.attempts`.
2. Publishes `provider_router.routing.failover` with the next index.
3. Moves to the next candidate, up to `failover_max_attempts`.

If every candidate in the bounded window fails, the router returns a
`ProviderInvocationOutcome` with `success=False` and the full attempt
trail. It does **not** raise on fail-over exhaustion.

### Retries

A transient failure from a single provider causes **Tool Manager's**
retry policy to fire first (configured per-adapter via the
Configuration Manager). The router's `retry_on_transient=True`
default additionally allows the router to try a different provider on
top of Tool Manager's retries. Both layers can be active at once.

### Empty chain

If the Capability Registry returns no candidates at all (nothing was
ever registered for the capability), the router raises
`NoProviderAvailableError`. This is the only fail-fast path -- it
indicates a configuration error, not a runtime one.

## Observability

Every routing decision is published to the Event Bus as a sequence of
`provider_router.*` events:

| Event | When |
| --- | --- |
| `provider_router.routing.started` | `route()` enters, before chain resolution |
| `provider_router.provider_attempt.started` | Just before each `tool_manager.invoke()` |
| `provider_router.provider_attempt.succeeded` | A candidate returned `status="completed"` |
| `provider_router.provider_attempt.failed` | A candidate returned `status="failed"` or raised |
| `provider_router.routing.failover` | Router moves to the next candidate |
| `provider_router.routing.succeeded` | `route()` returns with at least one successful attempt |
| `provider_router.routing.failed` | `route()` returns with all attempts failed |

The terminal event's payload includes the full `attempts` list so a
replay tool can reconstruct the decision from the event log alone.

Per-adapter observability (token usage, cost, latency, timeouts,
retries) is published separately by the adapters themselves under the
`tool_manager.provider.*` vocabulary. The router does not duplicate
those.

## Architecture constraints

- **No provider knowledge.** The router never imports an adapter, a
  provider name, or an HTTP/SSE/JSON-RPC concept. It speaks only to
  `ToolInvoker` and `CapabilitySelector` (Protocol-defined surfaces).
- **No Memory writes.** The router never calls the Memory Manager,
  Reflection Engine, Knowledge Graph, Context Builder, or Reasoning
  Engine.
- **No Commander dependency.** The router can be wired into any
  caller (not just Commander) that has a capability string.
- **No configuration.** The router has no configuration of its own;
  capability ranking, provider priority, retry policy, and timeout
  policy all flow through the Capability Registry and Tool Manager.

## Tests

`tests/test_service.py` covers:

- Empty chain raises `NoProviderAvailableError`.
- Single successful candidate returns `success=True`.
- Multi-candidate fail-over returns `success=True` on first match.
- Multi-candidate exhaustion returns `success=False` with all
  attempts recorded.
- Failed-then-succeeded ordering: the success attempt terminates the
  chain (no extra invocations).
- Streaming path yields the top-ranked provider's chunks.
- Event publication: `started`, `attempt_started`, `attempt_failed`,
  `failover`, `succeeded`, `failed`.
- Constructor rejects `failover_max_attempts < 1`.
- Missing `capability` raises `InvalidRoutingRequestError`.
- Registry lookup failure propagates with the `routing.failed` event
  published first.

## Future Considerations

- **Stream-level fail-over.** Today `route_stream()` is single-provider.
  Stream hand-off across providers requires partial-result buffering
  and cancellation; deferred until the Workspace demands it.
- **Cost-aware retry budget.** Currently the router bounds attempts
  by count. A cost-aware variant would bound by `Σ estimated_cost` and
  is a natural extension once the provider cost events land in the
  Logging System.