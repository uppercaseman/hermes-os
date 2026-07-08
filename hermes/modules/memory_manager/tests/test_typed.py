"""Tests for the Sprint-2 Cognitive Memory Architecture typed layer.

Three concerns, exercised end-to-end:

1. `MemoryEntry` gains the six typed fields
   (`memory_type`, `confidence`, `importance`, `provenance`,
   `superseded_by`, `relationships`) as additive, optional kwargs --
   every existing test (in `test_models.py`, `test_service.py`,
   `test_markdown.py`, `test_adapters.py`) keeps passing.
2. `MemoryManager` gains the new public surfaces -- `record_typed`,
   `mark_superseded`, `find_relationships`, `find_path` -- and
   the existing queries stay semantic (`query(memory_type=...)`
   filters; default `query()` hides superseded entries; etc.).
3. `migrate_memory_galaxy()` lifts the legacy scope+tags compatibility
   encoding (Sprint-1 Reflection Engine output) into first-class
   typed fields, idempotently.

The Reflection Engine's end-to-end wiring against a real
`MemoryManager` is covered in
`hermes/modules/reflection_engine/tests/test_integration_memory.py`.
"""
from __future__ import annotations

import uuid

import pytest

from hermes.modules.memory_manager import events as evt
from hermes.modules.memory_manager.interface import build_memory_manager
from hermes.modules.memory_manager.migration import migrate_memory_galaxy
from hermes.modules.memory_manager.typed import (
    ALL_MEMORY_TYPES,
    GraphPath,
    MemoryRelationship,
    MemoryRelationshipType,
    Provenance,
    REFLECTION_ENGINE_MANAGED_TAG,
    SUPERSEDED_TAG,
    default_tags_for_memory_type,
    is_memory_type,
    tag_for_memory_type,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

AGENT = "reflector"


def _provenance(source_id: str = "log-1", weight: float = 1.0) -> Provenance:
    return Provenance(
        source_type="log_entry",
        source_id=source_id,
        description="extracted from log",
        weight=weight,
    )


async def _capture_published(bus) -> list[str]:
    """Subscribes a sink handler to the bus's `"*"` wildcard and
    returns the event-type list captured. Each call subscribes a
    fresh handler so multiple captures in the same test do not
    accumulate stale entries."""
    captured: list[str] = []

    async def _sink(event) -> None:
        captured.append(event.event_type)

    await bus.subscribe("*", _sink)
    return captured


# --------------------------------------------------------------------------- #
# ALL_MEMORY_TYPES / is_memory_type
# --------------------------------------------------------------------------- #


class TestMemoryTypeConstants:
    def test_all_memory_types_is_six(self) -> None:
        """The six canonical cognitive memory types per
        `Memory Galaxy.md`. Knowledge Graph / Reflection Engine are
        deliberately not in this set (process / substrate, not stores).
        Decision / Error history are scope values, not memory types.
        """
        assert len(ALL_MEMORY_TYPES) == 6

    @pytest.mark.parametrize(
        "memory_type",
        [
            "user_dna",
            "working_memory",
            "mission_memory",
            "project_memory",
            "skill_memory",
            "experience_memory",
        ],
    )
    def test_all_memory_types_lists_canonical_six(self, memory_type: str) -> None:
        assert memory_type in ALL_MEMORY_TYPES

    def test_knowledge_graph_and_reflection_engine_are_not_memory_types(self) -> None:
        """Per `Memory Galaxy.md`: Knowledge Graph is the structural
        substrate (tags + backlinks + relationships); Reflection
        Engine is a process. Neither is a memory category that
        produces entries."""
        assert not is_memory_type("knowledge_graph")
        assert not is_memory_type("reflection_engine")

    def test_decision_and_error_are_not_memory_types(self) -> None:
        """Decision / Error History are scope values used by
        `record_decision` / `record_error`; they are NOT primary
        cognitive MemoryType values per the spec."""
        assert not is_memory_type("decision")
        assert not is_memory_type("error")

    def test_is_memory_type_validates_strings(self) -> None:
        assert is_memory_type("skill_memory")
        assert not is_memory_type("not_a_real_type")
        assert not is_memory_type("")


class TestTagHelpers:
    def test_tag_for_memory_type(self) -> None:
        assert tag_for_memory_type("skill_memory") == "memory:skill_memory"
        assert tag_for_memory_type("user_dna") == "memory:user_dna"

    def test_default_tags_for_memory_type_minimum(self) -> None:
        tags = default_tags_for_memory_type("skill_memory")
        assert REFLECTION_ENGINE_MANAGED_TAG in tags
        assert "memory:skill_memory" in tags
        # No origin tag without origin_mission_id.
        assert not any(t.startswith("reflection:origin:") for t in tags)

    def test_default_tags_for_memory_type_with_origin(self) -> None:
        mission_id = uuid.uuid4()
        tags = default_tags_for_memory_type("project_memory", origin_mission_id=mission_id)
        assert REFLECTION_ENGINE_MANAGED_TAG in tags
        assert "memory:project_memory" in tags
        assert f"reflection:origin:{mission_id}" in tags


# --------------------------------------------------------------------------- #
# MemoryEntry: typed fields
# --------------------------------------------------------------------------- #


class TestMemoryEntryTypedFields:
    def test_entry_with_typed_fields_constructs(self) -> None:
        from hermes.modules.memory_manager.models import MemoryEntry

        rel = MemoryRelationship(
            relationship_type=MemoryRelationshipType.DERIVED_FROM,
            target_entry_id=uuid.uuid4(),
            weight=0.7,
            description="synthesised from candidate",
        )
        prov = _provenance()
        entry = MemoryEntry(
            scope="persistent",
            key="k",
            value={},
            memory_type="skill_memory",
            confidence=0.9,
            importance=0.4,
            provenance=[prov],
            relationships=[rel],
        )
        assert entry.memory_type == "skill_memory"
        assert entry.confidence == 0.9
        assert entry.importance == 0.4
        assert entry.provenance[0].source_id == prov.source_id
        assert entry.relationships[0].relationship_type == MemoryRelationshipType.DERIVED_FROM

    def test_entry_without_typed_fields_constructs(self) -> None:
        """Sprint-1 behaviour: legacy entries without typed fields
        continue to construct exactly as before."""
        from hermes.modules.memory_manager.models import MemoryEntry

        entry = MemoryEntry(scope="persistent", key="k", value={})
        assert entry.memory_type is None
        assert entry.confidence is None
        assert entry.importance is None
        assert entry.provenance == []
        assert entry.relationships == []
        assert entry.superseded_by is None

    def test_confidence_bounds_enforced(self) -> None:
        from hermes.modules.memory_manager.models import MemoryEntry

        with pytest.raises(Exception):
            MemoryEntry(scope="persistent", key="k", value={}, confidence=1.5)
        with pytest.raises(Exception):
            MemoryEntry(scope="persistent", key="k", value={}, confidence=-0.1)


# --------------------------------------------------------------------------- #
# MemoryManager.record_typed
# --------------------------------------------------------------------------- #


class TestRecordTyped:
    async def test_record_typed_sets_first_class_memory_type(self, memory) -> None:
        entry = await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type="skill_memory",
            key="budget:alert:cost_threshold",
            value={"pattern": "alert when daily cost > $50"},
            confidence=0.9,
            importance=0.7,
            provenance=[_provenance()],
        )
        assert entry.memory_type == "skill_memory"
        assert entry.confidence == 0.9
        assert entry.importance == 0.7
        assert entry.provenance[0].source_id == "log-1"
        # Default tags stamped by `record_typed`.
        assert "memory:skill_memory" in entry.tags
        assert REFLECTION_ENGINE_MANAGED_TAG in entry.tags

    async def test_record_typed_default_scope_for_working_memory_is_session(self, memory) -> None:
        """`working_memory` is session-scoped per the spec; the
        other five typed destinations persist."""
        entry = await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type="working_memory",
            key="scratch",
            value={"frag": "latest user turn"},
        )
        assert entry.scope == "session"

    async def test_record_typed_default_scope_for_other_five_is_persistent(self, memory) -> None:
        for memory_type in (
            "user_dna",
            "mission_memory",
            "project_memory",
            "skill_memory",
            "experience_memory",
        ):
            entry = await memory.record_typed(
                requesting_agent_id=AGENT,
                memory_type=memory_type,  # type: ignore[arg-type]
                key=f"k:{memory_type}",
                value={},
            )
            assert entry.scope == "persistent", memory_type

    async def test_record_typed_explicit_scope_overrides(self, memory) -> None:
        entry = await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type="working_memory",
            scope="persistent",  # override
            key="scratch",
            value={},
        )
        assert entry.scope == "persistent"

    async def test_record_typed_rejects_unknown_memory_type(self, memory) -> None:
        with pytest.raises(ValueError):
            await memory.record_typed(
                requesting_agent_id=AGENT,
                memory_type="not_a_real_type",  # type: ignore[arg-type]
                key="k",
                value={},
            )

    async def test_record_typed_rejects_confidence_out_of_bounds(self, memory) -> None:
        with pytest.raises(ValueError):
            await memory.record_typed(
                requesting_agent_id=AGENT,
                memory_type="skill_memory",
                key="k",
                value={},
                confidence=1.5,
            )

    async def test_record_typed_relationship_persists(self, memory) -> None:
        skill = await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type="skill_memory",
            key="k1",
            value={},
        )
        experience = await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type="experience_memory",
            key="k2",
            value={},
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.RECORDED_DURING,
                    target_entry_id=skill.id,
                    weight=1.0,
                    description="happened during this run",
                )
            ],
        )
        assert experience.relationships[0].target_entry_id == skill.id

    async def test_record_typed_publishes_event(self, memory, bus) -> None:
        captured = await _capture_published(bus)
        ev_mgr = build_memory_manager(event_bus=bus)
        await ev_mgr.record_typed(
            requesting_agent_id=AGENT,
            memory_type="project_memory",
            key="k",
            value={},
        )
        assert evt.ENTRY_TYPED_RECORDED in captured
        assert evt.ENTRY_SAVED in captured

    async def test_query_with_memory_type_filter(self, memory) -> None:
        await memory.record_typed(requesting_agent_id=AGENT, memory_type="skill_memory", key="s", value={})
        await memory.record_typed(requesting_agent_id=AGENT, memory_type="project_memory", key="p", value={})
        # No filter returns both.
        all_entries = await memory.query(requesting_agent_id=AGENT)
        assert len(all_entries) == 2
        skill_entries = await memory.query(requesting_agent_id=AGENT, memory_type="skill_memory")
        assert len(skill_entries) == 1
        assert skill_entries[0].memory_type == "skill_memory"
        # Untyped entry wouldn't appear in memory_type-filtered query.
        await memory.save(requesting_agent_id=AGENT, scope="persistent", key="legacy", value={})
        project_entries = await memory.query(requesting_agent_id=AGENT, memory_type="project_memory")
        assert len(project_entries) == 1


# --------------------------------------------------------------------------- #
# MemoryManager.mark_superseded
# --------------------------------------------------------------------------- #


class TestMarkSuperseded:
    async def _two_typed_entries(self, memory, memory_type: str = "skill_memory"):
        old = await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type=memory_type,  # type: ignore[arg-type]
            key="old",
            value={"claim": "first version"},
        )
        new = await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type=memory_type,  # type: ignore[arg-type]
            key="new",
            value={"claim": "refined version"},
        )
        return old, new

    async def test_mark_superseded_sets_field_and_tag(self, memory) -> None:
        old, new = await self._two_typed_entries(memory)
        await memory.mark_superseded(
            requesting_agent_id=AGENT,
            entry_id=old.id,
            superseded_by=new.id,
        )
        # Original entry is still readable (additive-only rule).
        s = await memory.get(requesting_agent_id=AGENT, entry_id=old.id)
        assert s is not None
        assert s.superseded_by == new.id
        assert SUPERSEDED_TAG in s.tags

    async def test_default_query_hides_superseded(self, memory) -> None:
        old, new = await self._two_typed_entries(memory)
        await memory.mark_superseded(
            requesting_agent_id=AGENT,
            entry_id=old.id,
            superseded_by=new.id,
        )
        # Without `include_superseded=True`, only the new one shows up.
        visible = await memory.query(requesting_agent_id=AGENT, memory_type="skill_memory")
        assert {e.id for e in visible} == {new.id}

    async def test_query_include_superseded_returns_both(self, memory) -> None:
        old, new = await self._two_typed_entries(memory)
        await memory.mark_superseded(
            requesting_agent_id=AGENT,
            entry_id=old.id,
            superseded_by=new.id,
        )
        all_v = await memory.query(requesting_agent_id=AGENT, memory_type="skill_memory", include_superseded=True)
        assert {e.id for e in all_v} == {old.id, new.id}

    async def test_mark_superseded_is_idempotent(self, memory) -> None:
        old, new = await self._two_typed_entries(memory)
        await memory.mark_superseded(requesting_agent_id=AGENT, entry_id=old.id, superseded_by=new.id)
        # Second call is a no-op: still set, no additional tag, no error.
        await memory.mark_superseded(requesting_agent_id=AGENT, entry_id=old.id, superseded_by=new.id)
        s = await memory.get(requesting_agent_id=AGENT, entry_id=old.id)
        assert s is not None
        assert SUPERSEDED_TAG in s.tags
        # Single occurrence of the superseded tag (no duplicates added).
        assert s.tags.count(SUPERSEDED_TAG) == 1

    async def test_mark_superseded_rejects_self_loop(self, memory) -> None:
        e = await memory.record_typed(
            requesting_agent_id=AGENT, memory_type="skill_memory", key="k", value={}
        )
        with pytest.raises(ValueError):
            await memory.mark_superseded(requesting_agent_id=AGENT, entry_id=e.id, superseded_by=e.id)

    async def test_mark_superseded_publishes_event(self, memory, bus) -> None:
        captured = await _capture_published(bus)
        ev_mgr = build_memory_manager(event_bus=bus)
        old, new = await self._two_typed_entries(ev_mgr)
        await ev_mgr.mark_superseded(requesting_agent_id=AGENT, entry_id=old.id, superseded_by=new.id)
        assert evt.ENTRY_SUPERSEDED in captured


# --------------------------------------------------------------------------- #
# MemoryManager.find_relationships / find_path (Knowledge Graph substrate)
# --------------------------------------------------------------------------- #


class TestFindRelationships:
    async def _two_typed_entries(self, memory, memory_type: str = "skill_memory"):
        old = await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type=memory_type,  # type: ignore[arg-type]
            key="a",
            value={},
        )
        new = await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type=memory_type,  # type: ignore[arg-type]
            key="b",
            value={},
        )
        return old, new

    async def test_find_relationships_outbound(self, memory) -> None:
        a, b = await self._two_typed_entries(memory)
        await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type="experience_memory",
            key="c",
            value={},
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.RECORDED_DURING,
                    target_entry_id=a.id,
                    weight=0.5,
                ),
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.CONFIRMED_BY,
                    target_entry_id=b.id,
                    weight=0.9,
                ),
            ],
        )
        # Look up entry `c` (the source of both relationships).
        # Find its entry id and check outbound edges.
        es = await memory.query(requesting_agent_id=AGENT, memory_type="experience_memory")
        assert len(es) == 1
        c_id = es[0].id
        outbound = await memory.find_relationships(requesting_agent_id=AGENT, entry_id=c_id)
        assert len(outbound) == 2
        # Sorted by weight descending: b (0.9) before a (0.5).
        assert outbound[0].target_entry_id == b.id
        assert outbound[1].target_entry_id == a.id

    async def test_find_relationships_filter_by_type(self, memory) -> None:
        a, b = await self._two_typed_entries(memory)
        await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type="experience_memory",
            key="c",
            value={},
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.RECORDED_DURING,
                    target_entry_id=a.id,
                ),
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.CONFIRMED_BY,
                    target_entry_id=b.id,
                ),
            ],
        )
        es = await memory.query(requesting_agent_id=AGENT, memory_type="experience_memory")
        c_id = es[0].id
        only_confirmed = await memory.find_relationships(
            requesting_agent_id=AGENT,
            entry_id=c_id,
            relationship_type=MemoryRelationshipType.CONFIRMED_BY,
        )
        assert len(only_confirmed) == 1
        assert only_confirmed[0].target_entry_id == b.id

    async def test_find_relationships_inbound(self, memory) -> None:
        a, b = await self._two_typed_entries(memory)
        await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type="experience_memory",
            key="c",
            value={},
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.RECORDED_DURING,
                    target_entry_id=a.id,
                ),
            ],
        )
        # Inbound at `a` should be the edge from `c` to `a`.
        inbound = await memory.find_relationships(
            requesting_agent_id=AGENT,
            entry_id=a.id,
            direction="inbound",
        )
        assert len(inbound) == 1
        assert inbound[0].target_entry_id == a.id

    async def test_find_relationships_returns_empty_for_unknown_entry(self, memory) -> None:
        result = await memory.find_relationships(
            requesting_agent_id=AGENT,
            entry_id=uuid.uuid4(),
        )
        assert result == []


class TestFindPath:
    async def _two_typed_entries(self, memory, *, key_a: str = "alpha", key_b: str = "beta", memory_type: str = "skill_memory"):
        a = await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type=memory_type,  # type: ignore[arg-type]
            key=key_a,
            value={},
        )
        b = await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type=memory_type,  # type: ignore[arg-type]
            key=key_b,
            value={},
        )
        return a, b

    async def test_find_path_direct(self, memory) -> None:
        skill, _beta = await self._two_typed_entries(memory, key_a="skill_alpha", key_b="skill_beta")
        exp = await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type="experience_memory",
            key="exp_x",
            value={},
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.DERIVED_FROM,
                    target_entry_id=skill.id,
                )
            ],
        )
        path = await memory.find_path(
            requesting_agent_id=AGENT,
            from_id=exp.id,
            to_id=skill.id,
        )
        assert path.length == 1
        assert path.nodes[0] == exp.id and path.nodes[1] == skill.id
        assert path.edges == [MemoryRelationshipType.DERIVED_FROM]

    async def test_find_path_two_hop(self, memory) -> None:
        skill_a, skill_b = await self._two_typed_entries(memory, key_a="skill_a", key_b="skill_b")
        # experience -> skill_a -> skill_b
        experience = await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type="experience_memory",
            key="exp_x",
            value={},
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.DERIVED_FROM,
                    target_entry_id=skill_a.id,
                )
            ],
        )
        # Now add a typed relationship from skill_a -> skill_b by
        # re-writing skill_a (upsert semantics of `record_typed`).
        await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type="skill_memory",
            key="skill_a",
            value={},
            relationships=[
                MemoryRelationship(
                    relationship_type=MemoryRelationshipType.CONFIRMED_BY,
                    target_entry_id=skill_b.id,
                )
            ],
        )
        path = await memory.find_path(
            requesting_agent_id=AGENT,
            from_id=experience.id,
            to_id=skill_b.id,
        )
        assert path.length == 2
        assert path.nodes[0] == experience.id
        assert path.nodes[1] == skill_a.id
        assert path.nodes[2] == skill_b.id
        assert path.edges == [MemoryRelationshipType.DERIVED_FROM, MemoryRelationshipType.CONFIRMED_BY]

    async def test_find_path_no_path(self, memory) -> None:
        a, b = await self._two_typed_entries(memory, key_a="alpha", key_b="beta")
        path = await memory.find_path(requesting_agent_id=AGENT, from_id=a.id, to_id=b.id)
        assert path == GraphPath()
        assert path.length == 0
        assert path.nodes == []


# --------------------------------------------------------------------------- #
# migrate_memory_galaxy
# --------------------------------------------------------------------------- #


class TestMigrationShim:
    async def test_migrate_lifts_compatibility_encoded_entry(self, memory) -> None:
        legacy = await memory.save(
            requesting_agent_id=AGENT,
            scope="persistent",
            key="legacy:skill:foo",
            value={
                "confidence": 0.8,
                "importance": 0.6,
                "origin_mission_id": "11111111-1111-1111-1111-111111111111",
            },
            tags=["reflection_engine:managed", "reflection:skill"],
        )
        assert legacy.memory_type is None
        lifted = await migrate_memory_galaxy(memory)
        assert lifted == 1
        migrated = await memory.get(requesting_agent_id=AGENT, entry_id=legacy.id)
        assert migrated is not None
        assert migrated.memory_type == "skill_memory"
        assert migrated.confidence == 0.8
        assert migrated.importance == 0.6
        assert any(p.source_type == "synthetic" for p in migrated.provenance)

    async def test_migrate_idempotent_on_repeat_call(self, memory) -> None:
        await memory.save(
            requesting_agent_id=AGENT,
            scope="persistent",
            key="legacy:skill:foo",
            value={"confidence": 0.5},
            tags=["reflection_engine:managed", "reflection:skill"],
        )
        first = await migrate_memory_galaxy(memory)
        second = await migrate_memory_galaxy(memory)
        third = await migrate_memory_galaxy(memory)
        assert first == 1
        assert second == 0
        assert third == 0

    async def test_migrate_translates_all_four_destinations(self, memory) -> None:
        """All four Reflection Engine destinations map to canonical
        MemoryType values."""
        for destination, canonical in (
            ("user_dna", "user_dna"),
            ("skill", "skill_memory"),
            ("experience", "experience_memory"),
            ("project", "project_memory"),
        ):
            await memory.save(
                requesting_agent_id=AGENT,
                scope="persistent",
                key=f"legacy:{destination}:case",
                value={"confidence": 0.5},
                tags=["reflection_engine:managed", f"reflection:{destination}"],
            )
        lifted = await migrate_memory_galaxy(memory)
        assert lifted == 4
        # Verify canonical mappings.
        for canonical in ("user_dna", "skill_memory", "experience_memory", "project_memory"):
            entries = await memory.query(requesting_agent_id=AGENT, memory_type=canonical)  # type: ignore[arg-type]
            assert len(entries) == 1
            assert entries[0].memory_type == canonical

    async def test_migrate_no_op_on_unrecognised_entries(self, memory) -> None:
        """Entries without the legacy encoding aren't touched."""
        await memory.save(
            requesting_agent_id=AGENT,
            scope="persistent",
            key="plain",
            value={"data": "x"},
            tags=["totally:unrelated"],
        )
        # And one with no tags at all.
        await memory.save(
            requesting_agent_id=AGENT,
            scope="persistent",
            key="plain2",
            value={"data": "y"},
            tags=[],
        )
        lifted = await migrate_memory_galaxy(memory)
        assert lifted == 0
        # Originals unchanged.
        plain = await memory.get_by_key(requesting_agent_id=AGENT, scope="persistent", key="plain")
        assert plain is not None
        assert plain.memory_type is None

    async def test_migrate_preserves_tag_encoding(self, memory) -> None:
        """The tag encoding (`reflection:<destination>`,
        `reflection_engine:managed`) is preserved alongside the
        typed lift, so legacy code paths that filter by tag
        continue to work."""
        await memory.save(
            requesting_agent_id=AGENT,
            scope="persistent",
            key="legacy:skill:foo",
            value={"confidence": 0.5},
            tags=["reflection_engine:managed", "reflection:skill"],
        )
        await migrate_memory_galaxy(memory)
        migrated = await memory.get_by_key(requesting_agent_id=AGENT, scope="persistent", key="legacy:skill:foo")
        assert migrated is not None
        assert "reflection_engine:managed" in migrated.tags
        assert "reflection:skill" in migrated.tags

    async def test_migrate_publishes_completed_event(self, memory, bus) -> None:
        captured = await _capture_published(bus)
        ev_mgr = build_memory_manager(event_bus=bus)
        await ev_mgr.save(
            requesting_agent_id=AGENT,
            scope="persistent",
            key="legacy:skill:foo",
            value={},
            tags=["reflection_engine:managed", "reflection:skill"],
        )
        await migrate_memory_galaxy(ev_mgr)
        assert evt.MEMORY_GALAXY_MIGRATED in captured


# --------------------------------------------------------------------------- #
# MemoryScope unchanged
# --------------------------------------------------------------------------- #


class TestBackwardsCompatibility:
    async def test_legacy_save_still_works(self, memory) -> None:
        """Sprint-1 callers (no typed fields) keep working unchanged."""
        e = await memory.save(
            requesting_agent_id=AGENT,
            scope="persistent",
            key="k",
            value={"data": "v"},
            tags=["some", "tags"],
        )
        assert e.memory_type is None
        assert e.confidence is None
        assert e.tags == ["some", "tags"]

    async def test_legacy_save_still_rejects_decision_and_error_scopes(self, memory) -> None:
        with pytest.raises(ValueError):
            await memory.save(
                requesting_agent_id=AGENT,
                scope="decision",  # type: ignore[arg-type]
                key="d",
                value={},
            )
        with pytest.raises(ValueError):
            await memory.save(
                requesting_agent_id=AGENT,
                scope="error",  # type: ignore[arg-type]
                key="e",
                value={},
            )

    async def test_query_still_accepts_all_existing_kwargs(self, memory) -> None:
        """`memory_type` and `include_superseded` are added in
        Sprint-2. The existing kwargs (scope, tags, owner,
        session_id, workflow_run_id) still work as before."""
        await memory.record_typed(
            requesting_agent_id=AGENT,
            memory_type="skill_memory",
            key="k",
            value={},
            session_id="sess-1",
        )
        result = await memory.query(
            requesting_agent_id=AGENT,
            scope="persistent",
            session_id="sess-1",
        )
        assert len(result) == 1

    async def test_save_rejects_decision_scope_even_with_typed_fields(self, memory) -> None:
        with pytest.raises(ValueError):
            await memory.save(
                requesting_agent_id=AGENT,
                scope="decision",  # type: ignore[arg-type]
                key="d",
                value={},
                memory_type="user_dna",  # ignored -- the scope check fails first
            )
