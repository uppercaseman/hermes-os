"""Public entry point for the Memory Manager.

Everything outside this package imports from here, never from service.py
directly -- mirrors every other module's interface.py convention.
"""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.modules.memory_manager.contracts import MemoryBackend, VectorSearchProvider
from hermes.modules.memory_manager.errors import (
    MemoryPermissionDeniedError,
    UnknownMemoryEntryError,
    VectorSearchNotConfiguredError,
)
from hermes.modules.memory_manager.migration import migrate_memory_galaxy
from hermes.modules.memory_manager.models import MemoryEntry, MemoryPermissionGrant, MemoryScope
from hermes.modules.memory_manager.service import MemoryManager
from hermes.modules.memory_manager.typed import (
    ALL_MEMORY_TYPES,
    GraphPath,
    MemoryRelationship,
    MemoryRelationshipType,
    MemoryType,
    Provenance,
    REFLECTION_ENGINE_MANAGED_TAG,
    SUPERSEDED_TAG,
    all_memory_types,
    default_tags_for_memory_type,
    is_memory_type,
    tag_for_memory_type,
)

__all__ = [
    "MemoryManager",
    "MemoryEntry",
    "MemoryScope",
    "MemoryPermissionGrant",
    "MemoryBackend",
    "VectorSearchProvider",
    "UnknownMemoryEntryError",
    "MemoryPermissionDeniedError",
    "VectorSearchNotConfiguredError",
    "build_memory_manager",
    # Sprint-2 typed symbols
    "MemoryType",
    "ALL_MEMORY_TYPES",
    "all_memory_types",
    "is_memory_type",
    "Provenance",
    "MemoryRelationship",
    "MemoryRelationshipType",
    "GraphPath",
    "REFLECTION_ENGINE_MANAGED_TAG",
    "SUPERSEDED_TAG",
    "tag_for_memory_type",
    "default_tags_for_memory_type",
    "migrate_memory_galaxy",
]


def build_memory_manager(
    *,
    backend: MemoryBackend | None = None,
    vector_search: VectorSearchProvider | None = None,
    event_bus: EventBus | None = None,
) -> MemoryManager:
    return MemoryManager(backend=backend, vector_search=vector_search, event_bus=event_bus)
