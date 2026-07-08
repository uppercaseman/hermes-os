"""Local-mode EventBus: in-process asyncio pub/sub.

This is the "local" backend for the pluggable bus described in the
architecture doc. A cloud counterpart (Redis/NATS-backed) is a future
backend plugin implementing the same `EventBus` protocol -- nothing that
depends on `EventBus` should ever import this module directly.

Concurrency note: `subscribe`/`unsubscribe`/the handler-snapshot read in
`publish` all mutate or read the shared subscriber registry, and are
guarded by a single `asyncio.Lock`. Under today's single-threaded,
cooperative-scheduling implementation the registry operations never
actually interleave mid-statement, so the lock is not fixing an observed
bug -- it is making the safety property explicit and enforced, so it
keeps holding if this class ever gains a genuine `await` inside those
operations (e.g. a persistence-backed registry), and it documents by
example the guarantee any future networked implementation must uphold
(see interface.py). The lock is never held while handlers are actually
invoked, so a handler that itself calls `subscribe`/`unsubscribe` cannot
deadlock against it.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from hermes.core.event_bus.interface import EventHandler
from hermes.core.event_bus.models import Event

logger = logging.getLogger(__name__)


class InMemoryEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def publish(self, event: Event) -> None:
        """Delivers `event` to every handler subscribed to its
        `event_type`, plus every wildcard subscriber. See `EventBus` in
        interface.py for the full delivery contract this upholds."""
        async with self._lock:
            handlers = [*self._subscribers.get(event.event_type, []), *self._subscribers.get("*", [])]
        if not handlers:
            return
        # Each handler is isolated: one subscriber's bug must never stop
        # delivery to the others, and must never propagate back into the
        # publisher. This is the fault-tolerance boundary of the bus.
        await asyncio.gather(*(self._safe_invoke(handler, event) for handler in handlers))

    async def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Registers `handler` for `event_type` (or `"*"` for every
        event). Fully registered by the time this returns -- see the
        happens-before guarantee in interface.py."""
        async with self._lock:
            self._subscribers[event_type].append(handler)

    async def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Removes a previously-registered handler; a no-op if it is not
        currently registered."""
        async with self._lock:
            handlers = self._subscribers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

    @staticmethod
    async def _safe_invoke(handler: EventHandler, event: Event) -> None:
        try:
            await handler(event)
        except Exception:  # noqa: BLE001 -- isolate subscriber failures
            logger.exception("event handler raised for event_type=%s", event.event_type)
