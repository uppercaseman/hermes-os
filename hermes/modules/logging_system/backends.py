"""In-memory log storage backend -- the only `LogStorageBackend`
implementation so far. A future persistent one satisfies the same
Protocol; no database dependency is added here, per the brief.
"""
from __future__ import annotations

import uuid

from hermes.modules.logging_system.models import LogEntry


class InMemoryLogBackend:
    def __init__(self) -> None:
        self._entries: dict[uuid.UUID, LogEntry] = {}

    async def save(self, entry: LogEntry) -> None:
        self._entries[entry.id] = entry

    async def get(self, entry_id: uuid.UUID) -> LogEntry | None:
        return self._entries.get(entry_id)

    async def list_all(self) -> list[LogEntry]:
        return list(self._entries.values())
