import uuid

import pytest

from hermes.modules.memory_manager.adapters import NullVectorSearchProvider, ObsidianVaultAdapter
from hermes.modules.memory_manager.models import MemoryEntry


async def test_obsidian_adapter_write_is_unimplemented():
    adapter = ObsidianVaultAdapter(vault_path="/tmp/fake-vault")
    entry = MemoryEntry(scope="persistent", key="k", value={})

    with pytest.raises(NotImplementedError):
        await adapter.write_entry(entry)


async def test_obsidian_adapter_read_is_unimplemented():
    adapter = ObsidianVaultAdapter(vault_path="/tmp/fake-vault")

    with pytest.raises(NotImplementedError):
        await adapter.read_entry(uuid.uuid4())


async def test_obsidian_adapter_delete_is_unimplemented():
    adapter = ObsidianVaultAdapter(vault_path="/tmp/fake-vault")

    with pytest.raises(NotImplementedError):
        await adapter.delete_entry(uuid.uuid4())


async def test_null_vector_search_embed_is_unimplemented():
    provider = NullVectorSearchProvider()

    with pytest.raises(NotImplementedError):
        await provider.embed("some text")


async def test_null_vector_search_search_is_unimplemented():
    provider = NullVectorSearchProvider()

    with pytest.raises(NotImplementedError):
        await provider.search([0.1, 0.2], top_k=5)
