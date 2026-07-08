"""Placeholder Obsidian vault adapter.

Infrastructure only: actually reading/writing files in a real Obsidian
vault directory is intentionally not implemented -- "do not connect to
live external APIs yet" extends to local vault I/O here, consistent with
how every adapter in this codebase (Tool Manager's OpenAI/Claude/...
adapters) is a placeholder. The real, tested part of Obsidian
integration is markdown.py's `entry_to_markdown`, which a real
implementation of this adapter would call before writing the result to
a file under `vault_path`.
"""
from __future__ import annotations

import uuid

from hermes.modules.memory_manager.models import MemoryEntry


class ObsidianVaultAdapter:
    def __init__(self, *, vault_path: str) -> None:
        self.vault_path = vault_path

    async def write_entry(self, entry: MemoryEntry) -> None:
        raise NotImplementedError("ObsidianVaultAdapter is a placeholder -- vault I/O is not implemented.")

    async def read_entry(self, entry_id: uuid.UUID) -> MemoryEntry | None:
        raise NotImplementedError("ObsidianVaultAdapter is a placeholder -- vault I/O is not implemented.")

    async def delete_entry(self, entry_id: uuid.UUID) -> None:
        raise NotImplementedError("ObsidianVaultAdapter is a placeholder -- vault I/O is not implemented.")
