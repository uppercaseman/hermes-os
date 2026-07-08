from hermes.modules.capability_registry.models import CapabilityCandidate
from hermes.modules.capability_registry.strategies import PriorityCostLatencyStrategy


def _candidate(tool_name, priority=100, cost=0.0, latency=0.0, health="healthy"):
    return CapabilityCandidate(
        tool_name=tool_name, priority=priority, cost_per_call=cost, latency_ms=latency, health_state=health
    )


def test_ranks_by_priority_first():
    strategy = PriorityCostLatencyStrategy()
    candidates = [_candidate("b", priority=10), _candidate("a", priority=1)]

    ranked = strategy.rank(candidates)

    assert [c.tool_name for c in ranked] == ["a", "b"]


def test_healthy_beats_degraded_regardless_of_priority():
    strategy = PriorityCostLatencyStrategy()
    candidates = [
        _candidate("best-priority-but-degraded", priority=1, health="degraded"),
        _candidate("worse-priority-but-healthy", priority=50, health="healthy"),
    ]

    ranked = strategy.rank(candidates)

    assert ranked[0].tool_name == "worse-priority-but-healthy"


def test_cost_breaks_ties_at_equal_priority():
    strategy = PriorityCostLatencyStrategy()
    candidates = [_candidate("expensive", priority=1, cost=5.0), _candidate("cheap", priority=1, cost=0.5)]

    ranked = strategy.rank(candidates)

    assert ranked[0].tool_name == "cheap"


def test_latency_breaks_ties_at_equal_priority_and_cost():
    strategy = PriorityCostLatencyStrategy()
    candidates = [
        _candidate("slow", priority=1, cost=1.0, latency=500.0),
        _candidate("fast", priority=1, cost=1.0, latency=50.0),
    ]

    ranked = strategy.rank(candidates)

    assert ranked[0].tool_name == "fast"


def test_unknown_health_is_treated_like_healthy():
    strategy = PriorityCostLatencyStrategy()
    candidates = [_candidate("degraded", priority=1, health="degraded"), _candidate("unknown", priority=1, health="unknown")]

    ranked = strategy.rank(candidates)

    assert ranked[0].tool_name == "unknown"
