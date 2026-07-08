"""Notification Center unit tests."""
from __future__ import annotations

import uuid

import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.core.event_bus.models import Event
from hermes.modules.notification_center import build_notification_center
from hermes.modules.notification_center.contracts import (
    NotificationCenterProtocol,
    NotificationSink,
)
from hermes.modules.notification_center.errors import UnknownNotificationError


# ---------------------------------------------------------------------- #
# Direct raise API (no bus)
# ---------------------------------------------------------------------- #
class TestDirectRaise:
    def test_raise_notification_returns_record(self) -> None:
        nc = build_notification_center()
        n = nc.raise_notification(severity="info", title="hello")
        assert n.severity == "info"
        assert n.title == "hello"
        assert n.is_read is False
        assert n.is_dismissed is False

    def test_raise_notification_unread_increments(self) -> None:
        nc = build_notification_center()
        nc.raise_notification(severity="warning", title="a")
        nc.raise_notification(severity="warning", title="b")
        assert nc.unread_count() == 2
        assert nc.unread_count(severity="warning") == 2


# ---------------------------------------------------------------------- #
# Listing / filtering
# ---------------------------------------------------------------------- #
class TestListing:
    def test_list_filter_by_severity(self) -> None:
        nc = build_notification_center()
        nc.raise_notification(severity="info", title="i")
        nc.raise_notification(severity="warning", title="w")
        nc.raise_notification(severity="error", title="e")
        warnings = nc.list_notifications(severity="warning")
        assert [n.title for n in warnings] == ["w"]

    def test_list_unread_only(self) -> None:
        nc = build_notification_center()
        a = nc.raise_notification(severity="info", title="a")
        nc.raise_notification(severity="info", title="b")
        nc.mark_read(a.id)
        unread = nc.list_notifications(unread_only=True)
        assert [n.title for n in unread] == ["b"]

    def test_aggregate(self) -> None:
        nc = build_notification_center()
        nc.raise_notification(severity="info", title="i1")
        nc.raise_notification(severity="info", title="i2")
        nc.raise_notification(severity="warning", title="w1")
        agg = nc.aggregate()
        assert agg.total == 3
        assert agg.unread == 3
        assert agg.by_severity == {"info": 2, "warning": 1}


# ---------------------------------------------------------------------- #
# Read / dismiss / clear
# ---------------------------------------------------------------------- #
class TestMutations:
    def test_mark_read(self) -> None:
        nc = build_notification_center()
        n = nc.raise_notification(severity="info", title="a")
        marked = nc.mark_read(n.id)
        assert marked.is_read is True
        assert nc.unread_count() == 0

    def test_mark_read_unknown_raises(self) -> None:
        nc = build_notification_center()
        with pytest.raises(UnknownNotificationError):
            nc.mark_read(uuid.uuid4())

    def test_dismiss_sets_read_too(self) -> None:
        nc = build_notification_center()
        n = nc.raise_notification(severity="info", title="a")
        dismissed = nc.dismiss(n.id)
        assert dismissed.is_dismissed is True
        assert dismissed.is_read is True
        assert nc.unread_count() == 0

    def test_clear_all(self) -> None:
        nc = build_notification_center()
        nc.raise_notification(severity="info", title="i")
        nc.raise_notification(severity="warning", title="w")
        cleared = nc.clear()
        assert cleared == 2
        assert nc.unread_count() == 0
        assert nc.list_notifications() == []

    def test_clear_by_severity(self) -> None:
        nc = build_notification_center()
        nc.raise_notification(severity="info", title="i")
        nc.raise_notification(severity="warning", title="w")
        cleared = nc.clear(severity="info")
        assert cleared == 1
        assert len(nc.list_notifications(severity="info")) == 0
        assert len(nc.list_notifications(severity="warning")) == 1


# ---------------------------------------------------------------------- #
# Severity rules
# ---------------------------------------------------------------------- #
class TestSeverityRules:
    async def test_default_rule_error_for_failed(self) -> None:
        bus = InMemoryEventBus()
        nc = build_notification_center(event_bus=bus)
        await nc.start()
        ev = Event(
            event_type="x.y.failed",
            source_module="x",
            correlation_id=uuid.uuid4(),
            payload={},
        )
        await bus.publish(ev)
        notes = nc.list_notifications()
        assert len(notes) == 1
        assert notes[0].severity == "error"

    def test_register_severity_rule_overrides(self) -> None:
        nc = build_notification_center()
        nc.register_severity_rule("completed", "info")  # downgraded
        assert nc._classify("mission.completed") == "info"

    def test_register_severity_rule_adds_new(self) -> None:
        nc = build_notification_center()
        nc.register_severity_rule("mission.archived", "warning")
        assert nc._classify("mission.archived") == "warning"


# ---------------------------------------------------------------------- #
# Ring buffer bound
# ---------------------------------------------------------------------- #
class TestRingBuffer:
    def test_ring_drops_oldest(self) -> None:
        nc = build_notification_center(history_size=3)
        for i in range(5):
            nc.raise_notification(severity="info", title=f"n{i}")
        notes = nc.list_notifications()
        assert len(notes) == 3
        assert [n.title for n in notes] == ["n2", "n3", "n4"]

    def test_unread_count_consistent_across_eviction(self) -> None:
        nc = build_notification_center(history_size=2)
        a = nc.raise_notification(severity="info", title="a")
        b = nc.raise_notification(severity="info", title="b")
        # At this point unread = 2
        assert nc.unread_count() == 2
        nc.mark_read(a.id)
        assert nc.unread_count() == 1
        # Push a third to evict the now-read 'a'
        nc.raise_notification(severity="info", title="c")
        assert nc.unread_count() == 2  # b and c
        # Now evict b (still unread) by raising d
        nc.raise_notification(severity="info", title="d")
        assert nc.unread_count() == 2  # c and d (b got evicted; count adjusted)
        # b not in history; should raise
        with pytest.raises(UnknownNotificationError):
            nc.mark_read(b.id)


# ---------------------------------------------------------------------- #
# Bus integration
# ---------------------------------------------------------------------- #
class TestBusIntegration:
    async def test_wildcard_subscription(self) -> None:
        bus = InMemoryEventBus()
        nc = build_notification_center(event_bus=bus)
        await nc.start()
        await bus.publish(
            Event(
                event_type="mission.failed",
                source_module="mission_system",
                correlation_id=uuid.uuid4(),
                payload={"mission_id": "abc"},
            )
        )
        # Wait for delivery: not necessary (sync publish awaits all handlers).
        notes = nc.list_notifications(severity="error")
        assert len(notes) == 1
        assert notes[0].source_module == "mission_system"

    async def test_no_bus_does_not_raise(self) -> None:
        nc = build_notification_center(event_bus=None)
        await nc.start()  # no-op
        # Direct raise still works.
        n = nc.raise_notification(severity="info", title="x")
        assert n.title == "x"

    async def test_stop_unsubscribes(self) -> None:
        bus = InMemoryEventBus()
        nc = build_notification_center(event_bus=bus)
        await nc.start()
        await nc.stop()
        # After stop, no notifications get raised
        await bus.publish(
            Event(
                event_type="mission.failed",
                source_module="m",
                correlation_id=uuid.uuid4(),
                payload={},
            )
        )
        assert nc.list_notifications() == []


# ---------------------------------------------------------------------- #
# Snapshot / restore + protocol
# ---------------------------------------------------------------------- #
class TestSnapshotAndProtocol:
    def test_snapshot_and_restore(self) -> None:
        nc = build_notification_center()
        nc.raise_notification(severity="info", title="a")
        snap = nc.snapshot()
        nc2 = build_notification_center()
        nc2.restore(snap)
        assert len(nc2.list_notifications()) == 1

    def test_satisfies_protocols(self) -> None:
        nc = build_notification_center()
        assert isinstance(nc, NotificationCenterProtocol)
        assert isinstance(nc, NotificationSink)