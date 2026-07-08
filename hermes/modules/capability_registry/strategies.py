"""Built-in selection strategies.

This file is the extension point for "future automatic optimisation":
add a new `SelectionStrategy` implementation here (or anywhere) and pass
it to `CapabilityRegistry`/`build_capability_registry` -- nothing else
needs to change.
"""
from __future__ import annotations

from hermes.modules.capability_registry.models import CapabilityCandidate, ProviderHealthState

_HEALTH_RANK: dict[ProviderHealthState, int] = {"healthy": 0, "unknown": 0, "degraded": 1, "unavailable": 2}


class PriorityCostLatencyStrategy:
    """The default strategy: healthy candidates before degraded ones,
    then declared priority, then cost, then latency, all ascending
    (lower is better/preferred). Purely deterministic and provider-
    agnostic -- no learning, no history, which is exactly what makes it
    the right *default* rather than the final word on optimisation.
    """

    def rank(self, candidates: list[CapabilityCandidate]) -> list[CapabilityCandidate]:
        return sorted(
            candidates,
            key=lambda c: (_HEALTH_RANK.get(c.health_state, 1), c.priority, c.cost_per_call, c.latency_ms),
        )
