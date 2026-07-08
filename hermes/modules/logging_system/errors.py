"""Logging System-specific exception types.

Only `get_entry` (a single, specific id) raises for "not found" --
consistent with the rest of this codebase's convention that a query
returning a possibly-empty list never raises, only a lookup for one
specific, named thing does.
"""
from __future__ import annotations

import uuid


class UnknownLogEntryError(Exception):
    def __init__(self, entry_id: uuid.UUID) -> None:
        self.entry_id = entry_id
        super().__init__(f"no log entry with id {entry_id}")
