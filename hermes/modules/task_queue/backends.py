"""In-memory task storage backend -- the only `TaskStorageBackend`
implementation so far. A future SQLite/Postgres-backed one satisfies the
same Protocol (contracts.py); no database dependency is added here, per
the brief.
"""
from __future__ import annotations

import uuid

from hermes.modules.task_queue.models import QueuedTask


class InMemoryTaskBackend:
    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, QueuedTask] = {}

    async def save(self, task: QueuedTask) -> None:
        self._tasks[task.id] = task

    async def get(self, task_id: uuid.UUID) -> QueuedTask | None:
        return self._tasks.get(task_id)

    async def list_all(self) -> list[QueuedTask]:
        return list(self._tasks.values())
