"""Protocols the Memory Manager depends on. Both are optional
collaborators -- Memory Manager works entirely in-process without
either; they exist as the "future vector search" and "Obsidian vault
integration" hooks, not as required dependencies.
"""
from __future__ import annotations

import uuid
from typing import Protocol

from hermes.modules.memory_manager.models import MemoryEntry


class MemoryBackend(Protocol):
    """A place a MemoryEntry can be synced to/from beyond the in-process
    store. The Obsidian vault adapter (adapters/obsidian.py) is the
    first, placeholder example. A backend failure never fails the
    in-process save/delete that triggered it -- see service.py."""

    async def write_entry(self, entry: MemoryEntry) -> None: ...
    async def read_entry(self, entry_id: uuid.UUID) -> MemoryEntry | None: ...
    async def delete_entry(self, entry_id: uuid.UUID) -> None: ...


class VectorSearchProvider(Protocol):
    """Pluggable similarity search -- the "future vector search" hook.
    Nothing in this codebase computes a real embedding yet (see
    adapters/null_vector_search.py)."""

    async def embed(self, text: str) -> list[float]: ...
    async def search(self, query_embedding: list[float], *, top_k: int) -> list[tuple[uuid.UUID, float]]:
        """Returns `(entry_id, similarity_score)` pairs, best first."""
        ...
