"""Narrow Protocol for the Logging System's storage backend."""
from __future__ import annotations

import uuid
from typing import Protocol

from hermes.modules.logging_system.models import LogEntry


class LogStorageBackend(Protocol):
    """Durable storage for log entries. `InMemoryLogBackend` (backends.py)
    is the only implementation so far -- the extension point for a
    future persistent one, per "do not add database dependencies yet."
    """

    async def save(self, entry: LogEntry) -> None: ...
    async def get(self, entry_id: uuid.UUID) -> LogEntry | None: ...
    async def list_all(self) -> list[LogEntry]: ...
