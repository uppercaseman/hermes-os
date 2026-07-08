import uuid

import pytest

from hermes.core.supervisor import events as supervisor_events
from hermes.core.event_bus.models import Event
from hermes.modules.capability_registry.capabilities import REASONING
from hermes.modules.capability_registry.errors import UnknownCapabilityError, UnknownProviderError
from hermes.modules.capability_registry.events import SELECTION_MADE, SELECTION_UNAVAILABLE
from hermes.modules.capability_registry.interface import build_capability_registry
from hermes.modules.capability_registry.models import CapabilityCandidate, CapabilityProviderRegistration


def _reg(tool_name, priority=100, cost=0.0, latency=0.0):
    return CapabilityProviderRegistration(
        capability=REASONING, tool_name=tool_name, priority=priority, cost_per_call=cost, declared_latency_ms=latency
    )


def _supervisor_event(event_type: str, unit: str) -> Event:
    return Event(event_type=event_type, source_module="supervisor", correlation_id=uuid.uuid4(), payload={"unit": unit})


# --------------------------------------------------------------------- #
# Registration + basic selection
# --------------------------------------------------------------------- #

async def test_select_returns_highest_priority_provider(registry):
    registry.register_provider(_reg("claude", priority=10))
    registry.register_provider(_reg("openai", priority=1))

    selection = await registry.select(REASONING)

    assert selection.selected == "openai"
    assert [c.tool_name for c in selection.chain] == ["openai", "claude"]


async def test_select_raises_for_never_registered_capability(registry):
    with pytest.raises(UnknownCapabilityError):
        await registry.select("video_generation")


async def test_re_registering_same_pair_replaces_config(registry):
    registry.register_provider(_reg("openai", priority=50))
    registry.register_provider(_reg("openai", priority=1))  # config reload, not an error

    selection = await registry.select(REASONING)

    assert selection.chain[0].priority == 1


async def test_unregister_removes_a_provider_from_consideration(registry):
    registry.register_provider(_reg("openai", priority=1))
    registry.register_provider(_reg("claude", priority=2))

    registry.unregister_provider(REASONING, "openai")
    selection = await registry.select(REASONING)

    assert selection.selected == "claude"


# --------------------------------------------------------------------- #
# Fallback + health
# --------------------------------------------------------------------- #

async def test_unavailable_top_choice_falls_back_to_next(registry):
    registry.register_provider(_reg("openai", priority=1))
    registry.register_provider(_reg("claude", priority=2))
    await registry.update_health("openai", "unavailable")

    selection = await registry.select(REASONING)

    assert selection.selected == "claude"
    assert [c.tool_name for c in selection.chain] == ["claude"]  # unavailable is excluded entirely


async def test_all_providers_unavailable_returns_none_without_raising(registry):
    registry.register_provider(_reg("openai", priority=1))
    await registry.update_health("openai", "unavailable")

    selection = await registry.select(REASONING)

    assert selection.selected is None
    assert selection.chain == []
    assert "no available provider" in selection.reason


async def test_degraded_provider_is_still_selectable_but_deprioritized(registry):
    registry.register_provider(_reg("openai", priority=1))
    registry.register_provider(_reg("claude", priority=99))
    await registry.update_health("openai", "degraded")

    selection = await registry.select(REASONING)

    assert selection.selected == "claude"  # healthy beats degraded despite worse priority
    assert {c.tool_name for c in selection.chain} == {"openai", "claude"}  # both still in the chain


# --------------------------------------------------------------------- #
# Manual overrides
# --------------------------------------------------------------------- #

async def test_manual_disable_removes_provider_even_if_healthy(registry):
    registry.register_provider(_reg("openai", priority=1))
    registry.register_provider(_reg("claude", priority=2))

    await registry.set_provider_enabled("openai", False)
    selection = await registry.select(REASONING)

    assert selection.selected == "claude"


async def test_override_pins_a_specific_provider_regardless_of_ranking(registry):
    registry.register_provider(_reg("openai", priority=1))
    registry.register_provider(_reg("claude", priority=99))

    await registry.set_override(REASONING, "claude")
    selection = await registry.select(REASONING)

    assert selection.selected == "claude"
    assert selection.overridden is True


async def test_override_to_unregistered_provider_raises(registry):
    registry.register_provider(_reg("openai", priority=1))

    with pytest.raises(UnknownProviderError):
        await registry.set_override(REASONING, "not-a-real-provider")


async def test_override_conflicting_with_manual_disable_yields_no_selection(registry):
    registry.register_provider(_reg("openai", priority=1))
    await registry.set_override(REASONING, "openai")
    await registry.set_provider_enabled("openai", False)

    selection = await registry.select(REASONING)

    assert selection.selected is None
    assert selection.overridden is True
    assert "manually disabled" in selection.reason


async def test_clear_override_reverts_to_normal_ranking(registry):
    registry.register_provider(_reg("openai", priority=1))
    registry.register_provider(_reg("claude", priority=99))
    await registry.set_override(REASONING, "claude")

    await registry.clear_override(REASONING)
    selection = await registry.select(REASONING)

    assert selection.selected == "openai"
    assert selection.overridden is False


# --------------------------------------------------------------------- #
# Cost / latency
# --------------------------------------------------------------------- #

async def test_lower_cost_wins_at_equal_priority(registry):
    registry.register_provider(_reg("expensive", priority=1, cost=10.0))
    registry.register_provider(_reg("cheap", priority=1, cost=0.1))

    selection = await registry.select(REASONING)

    assert selection.selected == "cheap"


async def test_record_latency_overrides_declared_estimate_and_affects_ranking(registry):
    registry.register_provider(_reg("declared-fast", priority=1, latency=10.0))
    registry.register_provider(_reg("actually-faster", priority=1, latency=999.0))

    await registry.record_latency("actually-faster", 1.0)
    await registry.record_latency("actually-faster", 3.0)  # rolling average -> 2.0

    selection = await registry.select(REASONING)

    assert selection.selected == "actually-faster"
    winner = next(c for c in selection.chain if c.tool_name == "actually-faster")
    assert winner.latency_ms == 2.0


async def test_resolve_chain_returns_the_full_fallback_list(registry):
    registry.register_provider(_reg("a", priority=1))
    registry.register_provider(_reg("b", priority=2))
    registry.register_provider(_reg("c", priority=3))

    chain = await registry.resolve_chain(REASONING)

    assert [c.tool_name for c in chain] == ["a", "b", "c"]
    assert all(isinstance(c, CapabilityCandidate) for c in chain)


# --------------------------------------------------------------------- #
# Event bus integration
# --------------------------------------------------------------------- #

async def test_works_fully_standalone_without_an_event_bus(registry):
    """No event_bus was given -- every method must still work, publishing
    is simply a no-op."""
    registry.register_provider(_reg("openai", priority=1))
    await registry.update_health("openai", "healthy")
    await registry.set_override(REASONING, "openai")
    await registry.clear_override(REASONING)
    selection = await registry.select(REASONING)

    assert selection.selected == "openai"


async def test_select_publishes_selection_made_event(wired_registry, bus):
    received = []

    async def capture(event):
        received.append(event)

    await bus.subscribe(SELECTION_MADE, capture)
    wired_registry.register_provider(_reg("openai", priority=1))

    await wired_registry.select(REASONING)

    assert len(received) == 1
    assert received[0].payload["selected"] == "openai"


async def test_select_publishes_unavailable_event_when_nothing_selectable(wired_registry, bus):
    received = []

    async def capture(event):
        received.append(event)

    await bus.subscribe(SELECTION_UNAVAILABLE, capture)
    wired_registry.register_provider(_reg("openai", priority=1))
    await wired_registry.update_health("openai", "unavailable")

    await wired_registry.select(REASONING)

    assert len(received) == 1


async def test_start_auto_tracks_health_from_supervisor_events(wired_registry, bus):
    wired_registry.register_provider(_reg("openai", priority=1))
    wired_registry.register_provider(_reg("claude", priority=2))
    await wired_registry.start()

    await bus.publish(_supervisor_event(supervisor_events.UNIT_CRASHED, "openai"))
    selection = await wired_registry.select(REASONING)
    assert selection.selected == "claude"  # openai marked unavailable automatically

    await bus.publish(_supervisor_event(supervisor_events.UNIT_STARTED, "openai"))
    selection2 = await wired_registry.select(REASONING)
    assert selection2.selected == "openai"  # recovered automatically

    await wired_registry.stop()


async def test_stop_prevents_further_automatic_health_updates(wired_registry, bus):
    wired_registry.register_provider(_reg("openai", priority=1))
    await wired_registry.start()
    await wired_registry.stop()

    await bus.publish(_supervisor_event(supervisor_events.UNIT_CRASHED, "openai"))
    selection = await wired_registry.select(REASONING)

    assert selection.selected == "openai"  # crash event after stop() had no effect


async def test_unrelated_bus_events_are_ignored_by_the_wildcard_subscription(wired_registry, bus):
    wired_registry.register_provider(_reg("openai", priority=1))
    await wired_registry.start()

    await bus.publish(Event(event_type="commander.request.received", source_module="commander", correlation_id=uuid.uuid4(), payload={}))
    selection = await wired_registry.select(REASONING)

    assert selection.selected == "openai"  # unaffected
    await wired_registry.stop()


# --------------------------------------------------------------------- #
# Pluggable strategy (future automatic optimisation hook)
# --------------------------------------------------------------------- #

async def test_custom_strategy_can_be_swapped_in_without_changing_the_registry():
    class ReverseAlphabeticalStrategy:
        def rank(self, candidates):
            return sorted(candidates, key=lambda c: c.tool_name, reverse=True)

    registry = build_capability_registry(strategy=ReverseAlphabeticalStrategy())
    registry.register_provider(_reg("alpha", priority=1))
    registry.register_provider(_reg("zulu", priority=99))

    selection = await registry.select(REASONING)

    assert selection.selected == "zulu"  # priority ignored; the custom strategy decides
