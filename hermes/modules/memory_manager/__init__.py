"""Hermes Memory Manager: structured, permissioned, taggable memory
covering short-term conversation, long-term project, agent-owned,
workflow-run, decision-history, and error-history storage. See
interface.py and README.md.

Sprint-2 (Cognitive Memory Architecture) adds six first-class
cognitive memory types (user_dna, working_memory, mission_memory,
project_memory, skill_memory, experience_memory) plus typed
metadata (confidence, importance, provenance, superseded_by,
relationships) and the Knowledge Graph traversal helpers
(find_relationships, find_path). The typed extensions are
additive: every existing API surface keeps its signature.
"""

from hermes.modules.memory_manager.interface import (
    ALL_MEMORY_TYPES,
    GraphPath,
    MemoryEntry,
    MemoryManager,
    MemoryPermissionGrant,
    MemoryRelationship,
    MemoryRelationshipType,
    MemoryScope,
    MemoryType,
    Provenance,
    REFLECTION_ENGINE_MANAGED_TAG,
    SUPERSEDED_TAG,
    all_memory_types,
    build_memory_manager,
    default_tags_for_memory_type,
    is_memory_type,
    migrate_memory_galaxy,
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
