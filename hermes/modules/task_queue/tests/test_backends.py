import uuid

from hermes.modules.task_queue.backends import InMemoryTaskBackend
from hermes.modules.task_queue.models import QueuedTask


async def test_save_then_get_roundtrips():
    backend = InMemoryTaskBackend()
    task = QueuedTask()

    await backend.save(task)

    assert (await backend.get(task.id)).id == task.id


async def test_get_unknown_id_returns_none():
    backend = InMemoryTaskBackend()

    assert await backend.get(uuid.uuid4()) is None


async def test_list_all_returns_every_saved_task():
    backend = InMemoryTaskBackend()
    a, b = QueuedTask(), QueuedTask()

    await backend.save(a)
    await backend.save(b)

    ids = {t.id for t in await backend.list_all()}
    assert ids == {a.id, b.id}
