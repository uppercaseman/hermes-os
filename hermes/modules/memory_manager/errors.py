"""Memory Manager-specific exception types."""
from __future__ import annotations

import uuid


class UnknownMemoryEntryError(Exception):
    def __init__(self, entry_id: uuid.UUID) -> None:
        self.entry_id = entry_id
        super().__init__(f"no memory entry with id {entry_id}")


class MemoryPermissionDeniedError(Exception):
    def __init__(self, agent_id: str, owner_agent_id: str | None, permission: str) -> None:
        self.agent_id = agent_id
        self.owner_agent_id = owner_agent_id
        self.permission = permission
        owner_desc = "the shared pool" if owner_agent_id is None else f"agent {owner_agent_id!r}"
        super().__init__(f"agent {agent_id!r} does not have {permission!r} access to memory owned by {owner_desc}")


class VectorSearchNotConfiguredError(Exception):
    def __init__(self) -> None:
        super().__init__("no VectorSearchProvider was configured on this MemoryManager")
