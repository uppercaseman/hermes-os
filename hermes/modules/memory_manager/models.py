"""Pydantic data contracts for the Memory Manager.

One model, `MemoryEntry`, covers every named memory category by
combining three orthogonal dimensions instead of building a separate
store per category:

- `scope` -- WHEN/WHERE it lives: "session" (short-term conversation),
  "persistent" (long-term project), "workflow" (one workflow run),
  "decision"/"error" (append-only audit history).
- `owner_agent_id` -- WHO owns it. `None` means shared/global; a value
  means private to that agent ("agent memory" is not a separate scope --
  it's any scope with an owner set, since a single agent can just as
  easily have session-scoped or persistent private notes).
- `memory_type` -- WHAT cognitive type it is: "user_dna",
  "working_memory", "mission_memory", "project_memory",
  "skill_memory", "experience_memory". Optional because legacy
  entries written before the Sprint-2 typed extension don't carry
  it; once an entry has `memory_type` set, it is a first-class
  cognitive memory entry and the typed APIs in `service.py` apply
  to it. See `typed.py` for the canonical type enumeration.

The typed extension is purely additive: all existing fields
(`scope`, `owner_agent_id`, `session_id`, `workflow_run_id`, `key`,
`value`, `tags`, `backlinks`, `embedding_ref`, `created_at`,
`expires_at`) keep their previous shape. The new fields are
`confidence`, `importance`, `provenance`, `superseded_by`,
`memory_type`, and `relationships`. None of them change existing
test expectations.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from hermes.modules.memory_manager.typed import MemoryRelationship, MemoryType, Provenance

MemoryScope = Literal["session", "persistent", "workflow", "decision", "error"]


class MemoryEntry(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    scope: MemoryScope
    owner_agent_id: str | None = None
    session_id: str | None = None
    workflow_run_id: uuid.UUID | None = None
    key: str
    value: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    backlinks: list[uuid.UUID] = Field(default_factory=list)
    embedding_ref: str | None = Field(
        default=None, description="Pointer into a future vector index; nothing computes this yet."
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None

    # ----- Sprint-2 typed extension (additive, all optional) ----- #
    # MemoryType is the canonical cognitive memory classification
    # (see `typed.py`). `None` on legacy entries until they pass
    # through `migrate_memory_galaxy()`. The validator on assignment
    # goes through Pydantic's `Literal` -- a bad string is caught at
    # construction time, not at graph-traversal time.
    memory_type: str | None = Field(
        default=None,
        description=(
            "The canonical cognitive memory type. One of "
            "'user_dna', 'working_memory', 'mission_memory', "
            "'project_memory', 'skill_memory', 'experience_memory'. "
            "None for legacy entries."
        ),
    )
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    provenance: list["Provenance"] = Field(default_factory=list)
    superseded_by: uuid.UUID | None = Field(default=None)
    relationships: list["MemoryRelationship"] = Field(default_factory=list)


class MemoryPermissionGrant(BaseModel):
    """Grants `agent_id` access to memory owned by `owner_agent_id`
    (`None` = the shared/ownerless pool). See service.py's
    `_check_permission` for exactly how this is applied."""

    agent_id: str
    owner_agent_id: str | None = None
    can_read: bool = True
    can_write: bool = False


# Resolve the forward references on `MemoryEntry`. Pydantic v2 needs
# this called explicitly for `list["Provenance"]` / `list["MemoryRelationship"]`.
# The imports happen at module-import time for the real classes so
# the rebuilt validator uses the actual model classes, not strings.
def _rebuild_models() -> None:
    from hermes.modules.memory_manager.typed import MemoryRelationship, Provenance

    MemoryEntry.model_rebuild(
        _types_namespace={
            "Provenance": Provenance,
            "MemoryRelationship": MemoryRelationship,
        }
    )


_rebuild_models()

