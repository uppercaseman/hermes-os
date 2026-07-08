"""Placeholder Memory Manager backends -- infrastructure skeletons, not
real integrations. See the module README."""
from hermes.modules.memory_manager.adapters.null_vector_search import NullVectorSearchProvider
from hermes.modules.memory_manager.adapters.obsidian import ObsidianVaultAdapter

__all__ = ["ObsidianVaultAdapter", "NullVectorSearchProvider"]
