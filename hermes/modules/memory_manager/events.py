"""Event-type constants the Memory Manager publishes.

Namespaced `memory_manager.*`. All publishing is a no-op if the manager
was constructed without an event bus -- see service.py.
"""

ENTRY_SAVED = "memory_manager.entry.saved"
ENTRY_DELETED = "memory_manager.entry.deleted"
DECISION_RECORDED = "memory_manager.decision.recorded"
ERROR_RECORDED = "memory_manager.error.recorded"
BACKEND_SYNC_FAILED = "memory_manager.backend.sync_failed"
# Sprint-2 (Cognitive Memory Architecture) typed events. Subscribers
# can tell typed writes from generic `save` writes via
# `ENTRY_TYPED_RECORDED`. `ENTRY_SUPERSEDED` fires on every successful
# `mark_superseded(...)` call (idempotent re-applications are
# silenced). `MEMORY_GALAXY_MIGRATED` fires once per
# `migrate_memory_galaxy(...)` invocation regardless of how many
# entries were lifted.
ENTRY_TYPED_RECORDED = "memory_manager.entry.typed_recorded"
ENTRY_SUPERSEDED = "memory_manager.entry.superseded"
MEMORY_GALAXY_MIGRATED = "memory_manager.migration.completed"
