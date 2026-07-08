"""Provider Router -- capability-driven fail-over routing.

Resolves a **capability** request into one or more Tool Adapter
invocations, walking the ranked fallback chain provided by Capability
Registry and applying automatic fail-over, retries (when policy
permits), and routing-decision observability.

The router never invokes a provider directly. It dispatches through
`ToolManager.invoke(...)` and `ToolManager.invoke_stream(...)` so every
invocation inherits Tool Manager's existing retry, rate-limit, and
timeout infrastructure for free. This is the load-bearing boundary
that keeps the Provider Ecosystem layer clean: providers never see
Commander, and Commander never sees a provider name.

Per the Sprint-4 directive:

- **Provider-agnostic callers.** Commander asks the router for a
  capability. The router asks the Capability Registry which providers
  are available, healthy, and configured; walks the chain on transient
  failures; and reports a structured `ProviderInvocationOutcome`.

- **Routing events.** The router publishes `provider_router.*` events
  (`routing_started`, `routing_succeeded`, `routing_failed`,
  `provider_attempt_started`, `provider_attempt_failed`,
  `provider_attempt_succeeded`, `routing_failover`) so a future
  dashboard can replay every routing decision.
"""
from hermes.modules.provider_router.interface import build_provider_router
from hermes.modules.provider_router.service import ProviderRouter

__all__ = ["ProviderRouter", "build_provider_router"]
