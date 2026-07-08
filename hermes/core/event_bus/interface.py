"""Public contract for the event bus.

Every module talks to every other module only through this Protocol --
never through a direct import of another module's internals. Commander is
written against `EventBus`, not against any particular backend, so the
in-memory implementation here can be swapped for a Redis/NATS-backed one in
cloud deployments without touching Commander's code.

Delivery contract (binding on every implementation, in-memory or
distributed): this is what makes it safe to write orchestration logic like
`Commander._dispatch_and_await` -- "subscribe, then act, then await the
result" -- against ANY backend that satisfies this Protocol.

1. **Happens-before delivery.** If `subscribe(event_type, handler)` has
   returned before `publish(event)` is *invoked*, that publish is
   guaranteed to reach `handler`. A caller that awaits `subscribe()`
   before triggering the action that will eventually publish the matching
   event never has to worry about a same-tick race dropping it. (The
   in-memory implementation gets this for free from Python's cooperative
   scheduling; a networked backend must get it from its broker's
   subscribe-acknowledgment semantics before returning from `subscribe()`.)
2. **At-least-once, isolated delivery.** Every currently-subscribed
   handler for an event's type (plus every wildcard `"*"` subscriber) is
   invoked. One handler raising must never prevent another handler from
   being invoked, and must never propagate back into the publisher.
3. **No ordering guarantee across event types or subscribers.** Handlers
   for the same event may run concurrently; do not depend on one
   subscriber observing an event before another.
4. **Events must be safe to serialize.** `Event` is a Pydantic model with
   only JSON-safe field types specifically so any implementation can
   freely serialize it (`model_dump_json` / `model_validate_json`) across
   a process or network boundary without loss.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from hermes.core.event_bus.models import Event

EventHandler = Callable[[Event], Awaitable[None]]


class EventBus(Protocol):
    async def publish(self, event: Event) -> None:
        """Delivers `event` to every handler currently subscribed to its
        `event_type`, plus every wildcard (`"*"`) subscriber. See the
        module docstring for the exact delivery guarantees this must
        uphold."""
        ...

    async def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Registers `handler` for `event_type`. `event_type == "*"`
        subscribes to every event -- this is how the future Logging
        System module observes the whole system without any other module
        knowing it exists. Must not return until the registration is
        durably in effect (see the happens-before guarantee above)."""
        ...

    async def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Removes a previously-registered handler. A no-op if it was
        already removed or never registered."""
        ...
