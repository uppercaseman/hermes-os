"""Protocol for pluggable capability-selection strategies.

The default (`PriorityCostLatencyStrategy` in strategies.py) ranks
available candidates by health, then declared priority, then cost, then
latency. "Future automatic optimisation" -- one of this module's
requirements -- means swapping in a different `SelectionStrategy` (e.g.
one weighted by historical success rate) without changing
`CapabilityRegistry` itself.
"""
from __future__ import annotations

from typing import Protocol

from hermes.modules.capability_registry.models import CapabilityCandidate


class SelectionStrategy(Protocol):
    def rank(self, candidates: list[CapabilityCandidate]) -> list[CapabilityCandidate]:
        """Returns `candidates` sorted best-first. Candidates are already
        filtered to those that are not manually disabled and not marked
        unavailable -- a strategy only ranks, it never filters."""
        ...
