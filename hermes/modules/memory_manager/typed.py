"""Cognitive Memory Architecture — typed metadata for `MemoryEntry`.

This module adds the *what kind of cognitive memory is this* axis to
Memory Manager, alongside the existing scope (when/where it lives)
and owner (who owns it) dimensions. It does NOT replace either
dimension — `MemoryScope` ("session"/"persistent"/.../"decision"/
"error") and `owner_agent_id` (`None` or string) are unchanged, and
every existing test against them keeps passing.

The six canonical cognitive types mirror exactly the writable
memory categories defined in
`Specification/02 - Cognitive Architecture/Memory Galaxy.md`:

    user_dna           — durable facts about the user, true across projects
    working_memory     — session-scoped scratch space for one mission
    mission_memory     — permanent record of one mission (one entry per mission)
    project_memory     — durable facts scoped to one project
    skill_memory       — generalized patterns distiled across missions
    experience_memory  — specific past situations (one entry per notable event)

`Knowledge Graph` and `Reflection Engine` (the other two items in
the spec's "eight memory types" enumeration) are deliberately NOT
`MemoryType` values:

- **Reflection Engine** is a *process* — its presence in the eight-
  types list is a reminder that it sits inside Memory Galaxy, not a
  memory category that produces entries.
- **Knowledge Graph** is the *connective substrate* — tags,
  backlinks, and the typed `relationships` field below — not a
  separate store. No `MemoryEntry` with `memory_type=knowledge_graph`
  is ever written. The Memory Galaxy spec describes this explicitly:
  "Memory Manager's tagging and backlink mechanism is the graph's
  actual implementation substrate — the Knowledge Graph is a
  specification-level concept describing how that mechanism is used
  across memory types, not a separate storage engine."

Decision History and Error History (the existing
`record_decision`/`record_error` surface) are *historical record
categories* that flow through the legacy `scope="decision" /
"error"` namespace. They are deliberately NOT primary cognitive
`MemoryType` values, per the spec's enumeration.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# --------------------------------------------------------------------------- #
# The six canonical cognitive memory types
# --------------------------------------------------------------------------- #
#
# Kept as a strict `Literal` so a bad value is caught at type-check
# time, not at `record_typed(...)` runtime. The order matches the
# ordering in the `Memory Galaxy` mermaid diagram, which is the same
# ordering the spec uses.
#
# Why "working_memory" and not "working": the spec's name is "Working
# Memory" (two words). A literal in Python conventionally uses
# underscores between words, so `working_memory` matches the
# spec-name with the underscores serving as the word boundary.
MemoryType = Literal[
    "user_dna",
    "working_memory",
    "mission_memory",
    "project_memory",
    "skill_memory",
    "experience_memory",
]

# All canonical types, in the spec's enumeration order. Exposed as a
# helper so callers and tests iterate the closed set without
# redefining it.
ALL_MEMORY_TYPES: tuple[MemoryType, ...] = (
    "user_dna",
    "working_memory",
    "mission_memory",
    "project_memory",
    "skill_memory",
    "experience_memory",
)


def all_memory_types() -> list[MemoryType]:
    """A list-form mirror of `ALL_MEMORY_TYPES` for callers that
    prefer `list` over `tuple`. Exists so callers don't import the
    tuple and slice it."""
    return list(ALL_MEMORY_TYPES)


def is_memory_type(value: str) -> bool:
    """Runtime type-guard. Pairs with the `Literal` so call sites that
    accept a string from a wire payload (an event payload, a CLI
    argument) can validate before passing to `record_typed(...)`."""
    return value in ALL_MEMORY_TYPES


# --------------------------------------------------------------------------- #
# Metadata models
# --------------------------------------------------------------------------- #


class Provenance(BaseModel):
    """One source-of-evidence reference. Same shape the Reflection
    Engine already uses (`reflection_engine/models.py:Provenance`),
    so an engine-supplied provenance list drops into `record_typed`
    without rewriting.

    `source_type` distinguishes a log-entry reference (Logging
    System's path) from a memory-entry reference (Memory Manager's
    own path — a backreference into another `MemoryEntry`) from a
    synthetic source (the Reflection Engine's own synthesised
    attribution). The id is always a string because Logging System
    ids and Memory Manager ids are both uuid4 today but a future
    identifier scheme shouldn't require re-modelling."""

    source_type: Literal["memory_entry", "log_entry", "synthetic"]
    source_id: str
    description: str = ""
    weight: float = Field(default=1.0, ge=0.0)


class MemoryRelationshipType:
    """String constants for the directed typed edges that form the
    Knowledge Graph's first-class substrate. The KG is structural
    (per `Knowledge Graph.md`), and these relationships are the
    strongest structural edge — `backlinks` continues to work as
    the untyped looser-link list, `tags` continues to work as the
    facet-style classification, and `relationships` adds the
    explicit typed subgraph.

    Not stored as a `Literal` because the set is genuinely
    open-ended (a future schema.org-style registry might add many
    more) and pinning them now would force an ADR to extend."""

    # Reflection Engine origin -- "candidate X became entry Y."
    DERIVED_FROM = "derived_from"
    # Mission system writes -- "Y is the record of mission X."
    RECORDED_DURING = "recorded_during"
    # Project-scoping -- "Y belongs to project Z."
    BELONGS_TO_PROJECT = "belongs_to_project"
    # Contradiction -- "Y contradicts X." Distinct from supersession:
    # contradiction is a discovered conflict, supersession is a
    # written result. Both exist because a contradiction can sit in
    # the graph before any supersession has been decided.
    CONTRADICTS = "contradicts"
    # Temporal / causal -- "Y was caused by X."
    CAUSED_BY = "caused_by"
    # Cross-mission pattern -- "skill Y was confirmed across missions
    # X1, X2, ..." Used by Skill Memory to declare its contributing
    # missions as graph edges, not just value-embedded metadata.
    CONFIRMED_BY = "confirmed_by"
    # Project↔user -- "user X authored project decision Y."
    AUTHORED_BY = "authored_by"
    # Generic catch-all -- "Y references X." Use sparingly; prefer the
    # specific types above when one applies.
    REFERENCES = "references"

    ALL: tuple[str, ...] = (
        DERIVED_FROM,
        RECORDED_DURING,
        BELONGS_TO_PROJECT,
        CONTRADICTS,
        CAUSED_BY,
        CONFIRMED_BY,
        AUTHORED_BY,
        REFERENCES,
    )


# A simple alias so call sites don't have to import the holder class
# to type a relationship-type string.
RelationshipType = str


class MemoryRelationship(BaseModel):
    """One directed typed edge in the Knowledge Graph. Distinct from
    `MemoryEntry.backlinks`, which is the looser untyped list of
    uuid references the dashboard uses for reverse-edge lookups;
    a `MemoryRelationship` is the explicit *typed* edge.

    `direction` is always from the entry carrying this relationship
    to `target_entry_id`. The graph's reverse-edge is computed by
    `find_relationships(direction="inbound")` rather than stored
    twice — storing it twice would break the additive-only rule on
    the rare occasion the engine rewrites the forward edge."""

    relationship_type: str
    target_entry_id: uuid.UUID
    weight: float = Field(default=1.0, ge=0.0)
    description: str = ""

    @model_validator(mode="after")
    def _validate_known_type(self) -> MemoryRelationship:
        # A typed edge with an unknown type is allowed (the substrate
        # is open-ended; future ADR-defined types may exist on disk),
        # but warn via a comment in the validator's effect: we don't
        # raise, but the graph traversal helpers will quietly skip
        # edges whose types don't appear in their filter.
        return self


class GraphPath(BaseModel):
    """The result of `find_path(...)` — a sequence of entries linked
    by typed edges, plus the relationship-types that connect them.
    The `nodes` and `edges` lists are the same length, so
    `nodes[i+1]` is reached from `nodes[i]` via `edges[i]`. An
    empty `nodes` means "no path"."""

    nodes: list[uuid.UUID] = Field(default_factory=list)
    edges: list[str] = Field(default_factory=list)
    length: int = 0


# --------------------------------------------------------------------------- #
# Tag conventions
# --------------------------------------------------------------------------- #
#
# A small set of canonical tag prefixes the typed layer writes so that
# tags-based readers (legacy code, queries that don't know about
# `memory_type` yet) can still find entries by their role. The
# prefix doubles with the new first-class field so neither is a
# single source of truth — both are written; reads can pick either.
#
# RefleXtion_ENGINE_MANAGED_TAG is the legacy tag the Sprint-1
# Reflection Engine wrote; the migration shim recognises entries
# carrying this tag as candidates for typed lifte.
REFLECTION_ENGINE_MANAGED_TAG = "reflection_engine:managed"
SUPERSEDED_TAG = "superseded"


# --------------------------------------------------------------------------- #
# Submission helpers (used by `record_typed` and the migration shim)
# --------------------------------------------------------------------------- #


def tag_for_memory_type(memory_type: MemoryType) -> str:
    """The `memory:<memory_type>` tag every typed entry carries, so
    legacy tag-filter queries can scope to one cognitive type by
    tag. Mirrors the existing `destination_tag()` helper in the
    Reflection Engine's `models.py` for symmetry.
    """
    return f"memory:{memory_type}"


def default_tags_for_memory_type(
    memory_type: MemoryType, *, origin_mission_id: uuid.UUID | None = None
) -> list[str]:
    """The default tag set `record_typed` writes alongside the typed
    fields. A duplicate of the canonical field's signal so legacy
    readers don't lose data when migrating to typed fields.
    """
    tags = [REFLECTION_ENGINE_MANAGED_TAG, tag_for_memory_type(memory_type)]
    if origin_mission_id is not None:
        tags.append(f"reflection:origin:{origin_mission_id}")
    return tags


def now_utc() -> datetime:
    """A small wrapper around `datetime.now(timezone.utc)` for
    testability — tests monkey-patch this if they need to assert on
    timestamps."""
    return datetime.now(timezone.utc)
