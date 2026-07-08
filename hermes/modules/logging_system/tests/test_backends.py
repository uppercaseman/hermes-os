import uuid

from hermes.modules.logging_system.backends import InMemoryLogBackend
from hermes.modules.logging_system.models import LogEntry


async def test_save_then_get_roundtrips():
    backend = InMemoryLogBackend()
    entry = LogEntry(event_type="x", source_module="test", correlation_id=uuid.uuid4(), severity="info")

    await backend.save(entry)

    assert (await backend.get(entry.id)).id == entry.id


async def test_get_unknown_id_returns_none():
    backend = InMemoryLogBackend()

    assert await backend.get(uuid.uuid4()) is None


async def test_list_all_returns_every_saved_entry():
    backend = InMemoryLogBackend()
    a = LogEntry(event_type="a", source_module="test", correlation_id=uuid.uuid4(), severity="info")
    b = LogEntry(event_type="b", source_module="test", correlation_id=uuid.uuid4(), severity="info")

    await backend.save(a)
    await backend.save(b)

    ids = {e.id for e in await backend.list_all()}
    assert ids == {a.id, b.id}
