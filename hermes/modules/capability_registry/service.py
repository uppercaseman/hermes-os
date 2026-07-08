"""Capability Registry -- resolves a requested capability into a
specific Tool Adapter, so nothing in Hermes ever asks for a provider by
name.

This module makes no calls to Tool Manager, the Supervisor, or any
external API -- it is purely a selection framework:

- **Priority ordering**: `CapabilityProviderRegistration.priority`, lower
  preferred.
- **Fallback providers**: `select()` returns the *entire* ranked chain,
  not just the winner -- a caller can walk it if the top choice fails at
  actual invocation time.
- **Provider health**: tracked in `ProviderHealth`, either via explicit
  `update_health()` calls or, if this registry was given an event bus and
  `start()` was called, automatically from the Supervisor's
  `supervisor.unit.*` lifecycle events (a crashed/restarting/stopped unit
  becomes unavailable; a started one becomes healthy).
- **Provider cost / latency**: `cost_per_call` and `declared_latency_ms`
  are config; `record_latency()` feeds an observed rolling average that
  overrides the declared estimate once samples exist.
- **Manual override**: `set_override()` pins a capability to one
  provider regardless of ranking; `set_provider_enabled()` is an
  independent kill switch.
- **Future automatic optimisation**: the ranking itself is delegated to
  a pluggable `SelectionStrategy` (contracts.py/strategies.py) -- the
  default is deterministic (health, priority, cost, latency); a smarter,
  learning strategy can be swapped in later without touching this file.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.core.supervisor import events as supervisor_events
from hermes.modules.capability_registry import events as evt
from hermes.modules.capability_registry.contracts import SelectionStrategy
from hermes.modules.capability_registry.errors import UnknownCapabilityError, UnknownProviderError
from hermes.modules.capability_registry.models import (
    CapabilityCandidate,
    CapabilityProviderRegistration,
    CapabilitySelection,
    ProviderHealth,
    ProviderHealthState,
)
from hermes.modules.capability_registry.strategies import PriorityCostLatencyStrategy

SOURCE_MODULE = "capability_registry"

# How a Supervisor unit-lifecycle event maps onto provider health. Only
# events in this map are acted on; every other event (including this
# registry's own) is ignored by the wildcard subscription in `start()`.
_SUPERVISOR_EVENT_HEALTH: dict[str, ProviderHealthState] = {
    supervisor_events.UNIT_STARTED: "healthy",
    supervisor_events.UNIT_UNHEALTHY: "degraded",
    supervisor_events.UNIT_CRASHED: "unavailable",
    supervisor_events.UNIT_RESTARTING: "unavailable",
    supervisor_events.UNIT_RESTART_EXHAUSTED: "unavailable",
    supervisor_events.UNIT_RESTART_SKIPPED: "unavailable",
    supervisor_events.UNIT_STOPPED: "unavailable",
}


class CapabilityRegistry:
    def __init__(self, *, event_bus: EventBus | None = None, strategy: SelectionStrategy | None = None) -> None:
        self._bus = event_bus
        self._strategy = strategy or PriorityCostLatencyStrategy()
        self._registrations: dict[str, dict[str, CapabilityProviderRegistration]] = defaultdict(dict)
        self._health: dict[str, ProviderHealth] = {}
        self._overrides: dict[str, str] = {}
        self._disabled: set[str] = set()
        self._subscribed = False

    async def start(self) -> None:
        """If constructed with an event bus, subscribes to every event so
        Supervisor lifecycle events keep provider health current
        automatically (see `_SUPERVISOR_EVENT_HEALTH`). A no-op if no
        event bus was given -- health must then be updated via
        `update_health()` directly."""
        if self._bus is None or self._subscribed:
            return
        await self._bus.subscribe("*", self._on_bus_event)
        self._subscribed = True

    async def stop(self) -> None:
        """Undoes `start()`. A no-op if never started."""
        if self._bus is None or not self._subscribed:
            return
        await self._bus.unsubscribe("*", self._on_bus_event)
        self._subscribed = False

    # ------------------------------------------------------------------ #
    # Registration -- config, not runtime state. Sync and re-registration
    # is a silent replace (a config reload, not a caller mistake), unlike
    # Supervisor/ToolManager's raise-on-duplicate registration.
    # ------------------------------------------------------------------ #
    def register_provider(self, registration: CapabilityProviderRegistration) -> None:
        self._registrations[registration.capability][registration.tool_name] = registration
        self._health.setdefault(registration.tool_name, ProviderHealth(tool_name=registration.tool_name))

    def unregister_provider(self, capability: str, tool_name: str) -> None:
        self._registrations.get(capability, {}).pop(tool_name, None)

    # ------------------------------------------------------------------ #
    # Dynamic state
    # ------------------------------------------------------------------ #
    async def update_health(self, tool_name: str, state: ProviderHealthState, *, error: str | None = None) -> None:
        existing = self._health.get(tool_name, ProviderHealth(tool_name=tool_name))
        self._health[tool_name] = existing.model_copy(update={"state": state, "last_error": error})
        await self._publish(evt.HEALTH_UPDATED, {"tool_name": tool_name, "state": state})

    async def record_latency(self, tool_name: str, latency_ms: float) -> None:
        """Feeds one observed latency sample into a rolling average that
        overrides the registration's declared estimate. No event is
        published here -- this can fire once per real invocation, and
        the event log is not the place for that volume."""
        existing = self._health.get(tool_name, ProviderHealth(tool_name=tool_name))
        count = existing.sample_count
        new_avg = (
            latency_ms
            if existing.observed_latency_ms is None
            else (existing.observed_latency_ms * count + latency_ms) / (count + 1)
        )
        self._health[tool_name] = existing.model_copy(
            update={"observed_latency_ms": new_avg, "sample_count": count + 1}
        )

    async def set_override(self, capability: str, tool_name: str) -> None:
        """Pins `capability` to `tool_name`, bypassing ranking entirely.
        Raises `UnknownProviderError` if `tool_name` was never registered
        for `capability` -- an override can only pin to a declared
        candidate, not an arbitrary name."""
        if tool_name not in self._registrations.get(capability, {}):
            raise UnknownProviderError(capability, tool_name)
        self._overrides[capability] = tool_name
        await self._publish(evt.OVERRIDE_SET, {"capability": capability, "tool_name": tool_name})

    async def clear_override(self, capability: str) -> None:
        self._overrides.pop(capability, None)
        await self._publish(evt.OVERRIDE_CLEARED, {"capability": capability})

    async def set_provider_enabled(self, tool_name: str, enabled: bool) -> None:
        """Manual kill switch, independent of automatically-tracked
        health. A disabled provider is never selected, even if pinned via
        `set_override`."""
        if enabled:
            self._disabled.discard(tool_name)
        else:
            self._disabled.add(tool_name)
        await self._publish(evt.PROVIDER_ENABLED if enabled else evt.PROVIDER_DISABLED, {"tool_name": tool_name})

    # ------------------------------------------------------------------ #
    # Selection
    # ------------------------------------------------------------------ #
    async def select(self, capability: str) -> CapabilitySelection:
        """Resolves `capability` to a specific provider. Raises
        `UnknownCapabilityError` if nothing was ever registered for it;
        otherwise always returns a `CapabilitySelection` -- `selected` is
        `None` (with `reason` set) if every registered provider is
        currently disabled or unavailable, never a raised exception for
        that runtime condition."""
        registrations = self._registrations.get(capability)
        if not registrations:
            raise UnknownCapabilityError(capability)

        override_tool = self._overrides.get(capability)
        if override_tool is not None:
            selection = await self._select_override(capability, override_tool, registrations)
        else:
            selection = self._select_ranked(capability, registrations)

        await self._publish(
            evt.SELECTION_MADE if selection.selected else evt.SELECTION_UNAVAILABLE,
            {"capability": capability, "selected": selection.selected, "overridden": selection.overridden},
        )
        return selection

    async def resolve_chain(self, capability: str) -> list[CapabilityCandidate]:
        """The full ordered fallback chain for `capability`, for a caller
        that wants to try more than just the top selection after the
        first choice fails at actual invocation time."""
        return (await self.select(capability)).chain

    async def _select_override(
        self, capability: str, tool_name: str, registrations: dict[str, CapabilityProviderRegistration]
    ) -> CapabilitySelection:
        if tool_name in self._disabled:
            return CapabilitySelection(
                capability=capability,
                selected=None,
                overridden=True,
                reason=f"override pins {tool_name!r} but it is manually disabled",
            )
        candidate = self._build_candidate(registrations[tool_name])
        return CapabilitySelection(capability=capability, selected=tool_name, chain=[candidate], overridden=True)

    def _select_ranked(
        self, capability: str, registrations: dict[str, CapabilityProviderRegistration]
    ) -> CapabilitySelection:
        candidates = [
            self._build_candidate(reg)
            for name, reg in registrations.items()
            if name not in self._disabled and self._health_of(name).state != "unavailable"
        ]
        if not candidates:
            return CapabilitySelection(
                capability=capability, selected=None, reason="no available provider for this capability"
            )
        ranked = self._strategy.rank(candidates)
        return CapabilitySelection(capability=capability, selected=ranked[0].tool_name, chain=ranked)

    def _build_candidate(self, registration: CapabilityProviderRegistration) -> CapabilityCandidate:
        health = self._health_of(registration.tool_name)
        latency = health.observed_latency_ms if health.observed_latency_ms is not None else registration.declared_latency_ms
        return CapabilityCandidate(
            tool_name=registration.tool_name,
            priority=registration.priority,
            cost_per_call=registration.cost_per_call,
            latency_ms=latency,
            health_state=health.state,
        )

    def _health_of(self, tool_name: str) -> ProviderHealth:
        return self._health.get(tool_name, ProviderHealth(tool_name=tool_name))

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    async def _on_bus_event(self, event: Event) -> None:
        state = _SUPERVISOR_EVENT_HEALTH.get(event.event_type)
        if state is None:
            return
        tool_name = event.payload.get("unit")
        if not tool_name:
            return
        await self.update_health(tool_name, state)

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(event_type=event_type, source_module=SOURCE_MODULE, correlation_id=uuid.uuid4(), payload=payload)
        )
