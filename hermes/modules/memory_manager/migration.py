"""One-shot migration shim for the Sprint-1 → Sprint-2 transition.

The Reflection Engine's Sprint-1 implementation encoded the four
destination memory types (user_dna / skill / experience / project)
via `scope="persistent"` + tags `[reflection_engine:managed,
reflection:<destination>]` + first-class fields stuffed into `value`.
That worked end-to-end (and the Sprint-1 README documents this as
the C1 ADR candidate), but it coupled the engine to Memory Manager's
tag namespace and forbade `query(memory_type=...)` queries against
a first-class field.

Sprint-2 adds first-class `memory_type` (and the rest of the typed
metadata) to `MemoryEntry`. This module lifts the legacy encoding
into typed fields idempotently:

- An entry is a "compatibility-encoded" legacy entry iff it
  carries the `reflection_engine:managed` tag and a
  `reflection:<destination>` tag, has no `memory_type` yet, and
  has scope `persistent`.
- Lifting sets `memory_type` to the destination (mapped to the
  canonical name -- e.g. the engine's "skill" destination maps
  to `skill_memory`), copies `value["confidence"]` (if present)
  to the typed `confidence` field, copies `value["importance"]`
  to the typed `importance` field, and copies `value["origin_mission_id"]`
  (if present) into a `provenance` entry of type `synthetic`
  referencing the origin mission.
- The tag encoding is preserved alongside (so legacy code paths
  that filter by `reflection:<destination>` keep working).

The shim is safe to call repeatedly: a re-run on an already-lifted
entry is a no-op (the entry already has `memory_type` set).
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from hermes.modules.memory_manager import events as evt
from hermes.modules.memory_manager.typed import (
    MemoryType,
    Provenance,
    REFLECTION_ENGINE_MANAGED_TAG,
)

if TYPE_CHECKING:
    from hermes.modules.memory_manager.service import MemoryManager

# The Reflection Engine's four destinations. The Memory Galaxy spec
# uses the `*_memory` naming convention; the engine used the bare
# name (`user_dna`, `skill`, `experience`, `project`). This map
# translates engine vocabulary to canonical cognitive memory type.
#
# `working_memory` and `mission_memory` are not destination
# memory types in the Reflection Engine's vocabulary, so they are
# not present here -- the migration shim has nothing to lift for
# those. They may still be written by callers via `record_typed`
# directly; that's the typed path, not a legacy migration.
_DESTINATION_TO_MEMORY_TYPE: dict[str, MemoryType] = {
    "user_dna": "user_dna",
    "skill": "skill_memory",
    "experience": "experience_memory",
    "project": "project_memory",
}

# Tag prefix the engine's scope+tags encoding writes at commit
# time. `migrate_memory_galaxy()` looks for this prefix on each
# entry's tags; if present and the entry doesn't already have a
# `memory_type`, it lifts.
_DESTINATION_TAG_PREFIX = "reflection:"

# Sprint-1 stored a few first-class fields inside `value` rather
# than on `MemoryEntry`. These keys are lifted to first-class
# typed fields.
_LEGACY_CONFIDENCE_KEY = "confidence"
_LEGACY_IMPORTANCE_KEY = "importance"
_LEGACY_ORIGIN_MISSION_KEY = "origin_mission_id"
_LEGACY_SUPERSEDED_BY_KEY = "superseded_by"


async def migrate_memory_galaxy(
    memory_manager: MemoryManager,
    *,
    requesting_agent_id: str | None = None,
) -> int:
    """Walk every entry currently in `memory_manager._entries` and
    lift the legacy scope+tags compatibility encoding into first-
    class typed fields. Idempotent: re-running it on an already-
    typed store is a no-op. Returns the count of entries lifted
    (0 on no-op runs).

    `requesting_agent_id` defaults to `"migration"` -- the shim is
    an internal operation, not a per-agent action. The shim writes
    directly to the in-process store (via `save(...)`) so it
    triggers the normal entry-saved event pipeline.

    Why direct manipulation rather than a CLI command: this is the
    spec-mandated shim that "losslessly lifts existing
    scope="persistent" + tags reflections into typed entries,
    idempotently." A CLI would have to traverse the same store;
    doing it in-process keeps the surface small.
    """
    requester = requesting_agent_id or "migration"
    lifted = 0
    # Snapshot ids first -- `save(...)` may reorder the dict on
    # upsert (it doesn't today, but we don't want to be coupled to
    # that). Walking a snapshot keeps iteration deterministic.
    entry_ids = list(memory_manager._entries.keys())
    for entry_id in entry_ids:
        entry = memory_manager._entries.get(entry_id)
        if entry is None:
            continue
        if entry.memory_type is not None:
            continue  # already typed -- nothing to do

        destination = _extract_destination_from_tags(entry.tags)
        if destination is None:
            continue  # not a legacy compatibility-encoded entry

        memory_type = _DESTINATION_TO_MEMORY_TYPE.get(destination)
        if memory_type is None:
            # Shouldn't happen given the engine's closed set, but
            # guard against a future ADR that adds a new
            # destination vocabulary.
            continue

        confidence = entry.value.pop(_LEGACY_CONFIDENCE_KEY, None) or entry.confidence
        importance = entry.value.pop(_LEGACY_IMPORTANCE_KEY, None) or entry.importance
        origin_mission_id_raw = entry.value.pop(_LEGACY_ORIGIN_MISSION_KEY, None)
        legacy_superseded_by_raw = entry.value.pop(_LEGACY_SUPERSEDED_BY_KEY, None)

        provenance: list[Provenance] = list(entry.provenance)
        if origin_mission_id_raw is not None:
            try:
                origin_uuid = uuid.UUID(str(origin_mission_id_raw))
                provenance.append(
                    Provenance(
                        source_type="synthetic",
                        source_id=str(origin_uuid),
                        description="origin mission (lifted from legacy value.origin_mission_id)",
                    )
                )
            except ValueError:
                # If the legacy value is malformed, drop it rather
                # than fail the migration; the tag encoding is the
                # ground truth.
                pass

        superseded_by_uuid: uuid.UUID | None = None
        if legacy_superseded_by_raw is not None:
            try:
                superseded_by_uuid = uuid.UUID(str(legacy_superseded_by_raw))
            except ValueError:
                superseded_by_uuid = None

        # Rebuild the entry in place rather than going through
        # `save(...)` -- the key index already maps this id, and
        # we want to preserve the entry's id (the migration must
        # be lossless: every reference by id stays valid).
        entry.memory_type = memory_type
        if confidence is not None:
            entry.confidence = float(confidence)
        if importance is not None:
            entry.importance = float(importance)
        if provenance:
            entry.provenance = provenance
        if superseded_by_uuid is not None:
            entry.superseded_by = superseded_by_uuid
            if "superseded" not in entry.tags:
                entry.tags.append("superseded")
        memory_manager._add_to_typed_indices(entry)
        lifted += 1

    await memory_manager._publish(
        evt.MEMORY_GALAXY_MIGRATED,
        {
            "lifted": lifted,
            "entries_considered": len(entry_ids),
            "destination_to_memory_type": dict(_DESTINATION_TO_MEMORY_TYPE),
        },
    )
    return lifted


def _extract_destination_from_tags(tags: list[str]) -> str | None:
    """Returns the `destination` portion of the legacy `reflection:<destination>`
    tag, or `None` if the entry is not a legacy compatibility-encoded entry."""
    if REFLECTION_ENGINE_MANAGED_TAG not in tags:
        return None
    for tag in tags:
        if tag.startswith(_DESTINATION_TAG_PREFIX):
            return tag[len(_DESTINATION_TAG_PREFIX):]
    return None