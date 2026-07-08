import asyncio
import uuid

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.core.event_bus.models import Event


def _event(event_type: str, payload: dict | None = None) -> Event:
    return Event(
        event_type=event_type,
        source_module="test",
        correlation_id=uuid.uuid4(),
        payload=payload or {},
    )


async def test_subscriber_receives_published_event():
    bus = InMemoryEventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    await bus.subscribe("thing.happened", handler)
    await bus.publish(_event("thing.happened", {"x": 1}))

    assert len(received) == 1
    assert received[0].payload == {"x": 1}


async def test_subscriber_does_not_receive_other_event_types():
    bus = InMemoryEventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    await bus.subscribe("thing.happened", handler)
    await bus.publish(_event("other.thing"))

    assert received == []


async def test_wildcard_subscriber_receives_every_event():
    bus = InMemoryEventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    await bus.subscribe("*", handler)
    await bus.publish(_event("anything.at.all"))

    assert len(received) == 1


async def test_unsubscribe_stops_delivery():
    bus = InMemoryEventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    await bus.subscribe("thing.happened", handler)
    await bus.unsubscribe("thing.happened", handler)
    await bus.publish(_event("thing.happened"))

    assert received == []


async def test_one_handler_raising_does_not_stop_other_handlers():
    bus = InMemoryEventBus()
    received: list[Event] = []

    async def broken_handler(event: Event) -> None:
        raise RuntimeError("boom")

    async def good_handler(event: Event) -> None:
        received.append(event)

    await bus.subscribe("thing.happened", broken_handler)
    await bus.subscribe("thing.happened", good_handler)
    await bus.publish(_event("thing.happened"))

    assert len(received) == 1


async def test_publish_with_no_subscribers_does_not_raise():
    bus = InMemoryEventBus()
    await bus.publish(_event("nobody.listening"))


# --------------------------------------------------------------------- #
# Async / distributed-execution safety (architecture fix #2)
# --------------------------------------------------------------------- #

async def test_event_json_round_trip_preserves_all_fields():
    """Event must survive a JSON round trip unchanged -- this is what
    makes it safe to carry across a process/network boundary for a future
    distributed (Redis/NATS-backed) EventBus implementation."""
    original = _event("thing.happened", {"x": 1, "nested": {"y": 2}})

    restored = Event.model_validate_json(original.model_dump_json())

    assert restored == original


async def test_subscription_registered_before_publish_is_always_delivered():
    """The happens-before guarantee from interface.py: once `subscribe`
    has returned, a subsequent `publish` for that event type must never be
    missed. This is the exact property `Commander._dispatch_and_await`
    relies on when it subscribes before dispatching a task."""
    bus = InMemoryEventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    await bus.subscribe("thing.happened", handler)
    await bus.publish(_event("thing.happened"))

    assert len(received) == 1


async def test_concurrent_subscribe_and_publish_do_not_drop_or_corrupt_handlers():
    """Many coroutines subscribing and publishing at once must never drop
    a handler, deliver to a partially-registered list, or crash the bus --
    the safety bar for running the bus under real concurrent load."""
    bus = InMemoryEventBus()
    received: list[int] = []

    def make_handler(marker: int):
        async def handler(event: Event) -> None:
            received.append(marker)

        return handler

    handlers = [make_handler(i) for i in range(20)]
    await asyncio.gather(*(bus.subscribe("thing.happened", h) for h in handlers))

    await asyncio.gather(*(bus.publish(_event("thing.happened")) for _ in range(5)))

    assert len(received) == 20 * 5


async def test_unsubscribe_concurrent_with_publish_never_raises():
    bus = InMemoryEventBus()

    async def handler(event: Event) -> None:
        pass

    await bus.subscribe("thing.happened", handler)

    await asyncio.gather(
        bus.unsubscribe("thing.happened", handler),
        bus.publish(_event("thing.happened")),
    )
