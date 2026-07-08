import uuid

import pytest

from hermes.modules.memory_manager.errors import (
    MemoryPermissionDeniedError,
    UnknownMemoryEntryError,
    VectorSearchNotConfiguredError,
)
from hermes.modules.memory_manager.events import (
    BACKEND_SYNC_FAILED,
    DECISION_RECORDED,
    ENTRY_DELETED,
    ENTRY_SAVED,
)
from hermes.modules.memory_manager.interface import build_memory_manager
from hermes.modules.memory_manager.adapters import ObsidianVaultAdapter


# --------------------------------------------------------------------- #
# Structured save / retrieve
# --------------------------------------------------------------------- #

async def test_save_then_get_roundtrips(memory):
    saved = await memory.save(requesting_agent_id="agent-a", scope="persistent", key="notes", value={"a": 1})

    fetched = await memory.get(requesting_agent_id="agent-a", entry_id=saved.id)

    assert fetched is not None
    assert fetched.value == {"a": 1}


async def test_save_upserts_the_same_composite_key(memory):
    first = await memory.save(requesting_agent_id="agent-a", scope="session", session_id="s1", key="topic", value={"v": 1})
    second = await memory.save(requesting_agent_id="agent-a", scope="session", session_id="s1", key="topic", value={"v": 2})

    assert first.id == second.id
    fetched = await memory.get(requesting_agent_id="agent-a", entry_id=first.id)
    assert fetched.value == {"v": 2}


async def test_different_session_ids_do_not_collide(memory):
    a = await memory.save(requesting_agent_id="agent-a", scope="session", session_id="s1", key="topic", value={"v": 1})
    b = await memory.save(requesting_agent_id="agent-a", scope="session", session_id="s2", key="topic", value={"v": 2})

    assert a.id != b.id


async def test_get_returns_none_for_unknown_id(memory):
    assert await memory.get(requesting_agent_id="agent-a", entry_id=uuid.uuid4()) is None


async def test_get_returns_none_for_expired_entry(memory):
    saved = await memory.save(
        requesting_agent_id="agent-a", scope="session", key="ephemeral", value={}, ttl_seconds=-1
    )

    assert await memory.get(requesting_agent_id="agent-a", entry_id=saved.id) is None


async def test_get_by_key_finds_the_upserted_entry(memory):
    await memory.save(requesting_agent_id="agent-a", scope="persistent", key="topic", value={"v": 1})

    fetched = await memory.get_by_key(requesting_agent_id="agent-a", scope="persistent", key="topic")

    assert fetched is not None and fetched.value == {"v": 1}


async def test_save_rejects_decision_and_error_scopes(memory):
    with pytest.raises(ValueError):
        await memory.save(requesting_agent_id="agent-a", scope="decision", key="k", value={})
    with pytest.raises(ValueError):
        await memory.save(requesting_agent_id="agent-a", scope="error", key="k", value={})


# --------------------------------------------------------------------- #
# Query
# --------------------------------------------------------------------- #

async def test_query_filters_by_scope(memory):
    await memory.save(requesting_agent_id="a", scope="persistent", key="k1", value={})
    await memory.save(requesting_agent_id="a", scope="session", key="k2", value={})

    results = await memory.query(requesting_agent_id="a", scope="persistent")

    assert {e.key for e in results} == {"k1"}


async def test_query_filters_by_tags_using_and_semantics(memory):
    await memory.save(requesting_agent_id="a", scope="persistent", key="k1", value={}, tags=["x", "y"])
    await memory.save(requesting_agent_id="a", scope="persistent", key="k2", value={}, tags=["x"])

    results = await memory.query(requesting_agent_id="a", tags=["x", "y"])

    assert {e.key for e in results} == {"k1"}


async def test_query_filters_by_session_and_workflow(memory):
    run_id = uuid.uuid4()
    await memory.save(requesting_agent_id="a", scope="session", session_id="s1", key="k1", value={})
    await memory.save(requesting_agent_id="a", scope="workflow", workflow_run_id=run_id, key="k2", value={})

    by_session = await memory.query(requesting_agent_id="a", session_id="s1")
    by_workflow = await memory.query(requesting_agent_id="a", workflow_run_id=run_id)

    assert {e.key for e in by_session} == {"k1"}
    assert {e.key for e in by_workflow} == {"k2"}


async def test_query_excludes_expired_entries(memory):
    await memory.save(requesting_agent_id="a", scope="session", key="gone", value={}, ttl_seconds=-1)
    await memory.save(requesting_agent_id="a", scope="session", key="still-here", value={})

    results = await memory.query(requesting_agent_id="a", scope="session")

    assert {e.key for e in results} == {"still-here"}


# --------------------------------------------------------------------- #
# Delete + backlinks
# --------------------------------------------------------------------- #

async def test_delete_removes_the_entry(memory):
    saved = await memory.save(requesting_agent_id="a", scope="persistent", key="k", value={})

    await memory.delete(requesting_agent_id="a", entry_id=saved.id)

    assert await memory.get(requesting_agent_id="a", entry_id=saved.id) is None


async def test_delete_unknown_entry_raises(memory):
    with pytest.raises(UnknownMemoryEntryError):
        await memory.delete(requesting_agent_id="a", entry_id=uuid.uuid4())


async def test_get_backlinks_finds_referencing_entries(memory):
    target = await memory.save(requesting_agent_id="a", scope="persistent", key="target", value={})
    referrer = await memory.save(
        requesting_agent_id="a", scope="persistent", key="referrer", value={}, backlinks=[target.id]
    )

    backlinks = await memory.get_backlinks(requesting_agent_id="a", entry_id=target.id)

    assert [e.id for e in backlinks] == [referrer.id]


async def test_deleted_entry_no_longer_appears_in_backlinks(memory):
    target = await memory.save(requesting_agent_id="a", scope="persistent", key="target", value={})
    referrer = await memory.save(
        requesting_agent_id="a", scope="persistent", key="referrer", value={}, backlinks=[target.id]
    )

    await memory.delete(requesting_agent_id="a", entry_id=referrer.id)

    assert await memory.get_backlinks(requesting_agent_id="a", entry_id=target.id) == []


# --------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------- #

async def test_owner_has_full_access_to_own_memory(memory):
    saved = await memory.save(requesting_agent_id="agent-a", scope="persistent", owner_agent_id="agent-a", key="k", value={})

    fetched = await memory.get(requesting_agent_id="agent-a", entry_id=saved.id)
    assert fetched is not None
    await memory.delete(requesting_agent_id="agent-a", entry_id=saved.id)  # does not raise


async def test_other_agent_cannot_read_private_memory_without_a_grant(memory):
    saved = await memory.save(requesting_agent_id="agent-a", scope="persistent", owner_agent_id="agent-a", key="k", value={})

    with pytest.raises(MemoryPermissionDeniedError):
        await memory.get(requesting_agent_id="agent-b", entry_id=saved.id)


async def test_grant_unlocks_read_access(memory):
    saved = await memory.save(requesting_agent_id="agent-a", scope="persistent", owner_agent_id="agent-a", key="k", value={})
    memory.grant_permission("agent-b", owner_agent_id="agent-a", can_read=True)

    fetched = await memory.get(requesting_agent_id="agent-b", entry_id=saved.id)

    assert fetched is not None


async def test_grant_with_write_unlocks_write_access(memory):
    memory.grant_permission("agent-b", owner_agent_id="agent-a", can_read=True, can_write=True)

    saved = await memory.save(
        requesting_agent_id="agent-b", scope="persistent", owner_agent_id="agent-a", key="k", value={}
    )

    assert saved.owner_agent_id == "agent-a"


async def test_write_without_write_grant_is_denied(memory):
    memory.grant_permission("agent-b", owner_agent_id="agent-a", can_read=True, can_write=False)

    with pytest.raises(MemoryPermissionDeniedError):
        await memory.save(requesting_agent_id="agent-b", scope="persistent", owner_agent_id="agent-a", key="k", value={})


async def test_shared_memory_is_readable_and_writable_by_default(memory):
    """No owner_agent_id means the shared pool -- open by default, no
    grant required, so the most basic save/retrieve use case needs no
    permission setup at all. Grants exist for restricting access to a
    SPECIFIC agent's private memory, not for gating the shared pool."""
    saved = await memory.save(requesting_agent_id="agent-a", scope="persistent", key="k", value={})

    fetched = await memory.get(requesting_agent_id="agent-b", entry_id=saved.id)

    assert fetched is not None


async def test_revoke_permission_removes_access(memory):
    saved = await memory.save(requesting_agent_id="agent-a", scope="persistent", owner_agent_id="agent-a", key="k", value={})
    memory.grant_permission("agent-b", owner_agent_id="agent-a", can_read=True)
    memory.revoke_permission("agent-b", owner_agent_id="agent-a")

    with pytest.raises(MemoryPermissionDeniedError):
        await memory.get(requesting_agent_id="agent-b", entry_id=saved.id)


async def test_query_silently_excludes_unreadable_entries_rather_than_raising(memory):
    await memory.save(requesting_agent_id="agent-a", scope="persistent", owner_agent_id="agent-a", key="k", value={})

    results = await memory.query(requesting_agent_id="agent-b")

    assert results == []  # no exception, just excluded


# --------------------------------------------------------------------- #
# Decision / error history
# --------------------------------------------------------------------- #

async def test_record_decision_always_creates_a_new_entry(memory):
    first = await memory.record_decision(agent_id="agent-a", summary="chose provider X")
    second = await memory.record_decision(agent_id="agent-a", summary="chose provider X")

    assert first.id != second.id  # append-only, never upserted


async def test_decision_history_is_ordered_and_respects_limit(memory):
    await memory.record_decision(agent_id="agent-a", summary="first")
    await memory.record_decision(agent_id="agent-a", summary="second")
    await memory.record_decision(agent_id="agent-a", summary="third")

    history = await memory.get_decision_history(requesting_agent_id="agent-a", owner_agent_id="agent-a", limit=2)

    assert [e.key for e in history] == ["second", "third"]


async def test_error_history_is_tracked_separately_from_decisions(memory):
    await memory.record_decision(agent_id="agent-a", summary="a decision")
    await memory.record_error(agent_id="agent-a", summary="an error")

    decisions = await memory.get_decision_history(requesting_agent_id="agent-a", owner_agent_id="agent-a")
    errors = await memory.get_error_history(requesting_agent_id="agent-a", owner_agent_id="agent-a")

    assert [e.key for e in decisions] == ["a decision"]
    assert [e.key for e in errors] == ["an error"]


async def test_decision_history_respects_permissions(memory):
    await memory.record_decision(agent_id="agent-a", summary="private decision")

    history = await memory.get_decision_history(requesting_agent_id="agent-b", owner_agent_id="agent-a")

    assert history == []  # agent-b has no grant


# --------------------------------------------------------------------- #
# Housekeeping
# --------------------------------------------------------------------- #

async def test_sweep_expired_reclaims_and_reports_count(memory):
    await memory.save(requesting_agent_id="a", scope="session", key="gone", value={}, ttl_seconds=-1)
    await memory.save(requesting_agent_id="a", scope="session", key="still-here", value={})

    removed = await memory.sweep_expired()

    assert removed == 1
    assert len(await memory.query(requesting_agent_id="a")) == 1


# --------------------------------------------------------------------- #
# Future vector search
# --------------------------------------------------------------------- #

async def test_search_similar_raises_when_not_configured(memory):
    with pytest.raises(VectorSearchNotConfiguredError):
        await memory.search_similar(requesting_agent_id="a", query_text="hello")


async def test_search_similar_returns_matching_entries_respecting_permissions():
    saved_entries = {}

    class FakeVectorSearch:
        async def embed(self, text):
            return [0.1, 0.2]

        async def search(self, query_embedding, *, top_k):
            return [(entry_id, 0.9) for entry_id in saved_entries][:top_k]

    memory = build_memory_manager(vector_search=FakeVectorSearch())
    visible = await memory.save(requesting_agent_id="a", scope="persistent", owner_agent_id="a", key="k1", value={})
    hidden = await memory.save(requesting_agent_id="b", scope="persistent", owner_agent_id="b", key="k2", value={})
    saved_entries[visible.id] = True
    saved_entries[hidden.id] = True

    results = await memory.search_similar(requesting_agent_id="a", query_text="q", top_k=5)

    assert [e.id for e in results] == [visible.id]  # hidden entry excluded by permission check


# --------------------------------------------------------------------- #
# Events + resilience
# --------------------------------------------------------------------- #

async def test_save_publishes_entry_saved_event(bus):
    memory = build_memory_manager(event_bus=bus)
    received = []

    async def capture(event):
        received.append(event)

    await bus.subscribe(ENTRY_SAVED, capture)
    await memory.save(requesting_agent_id="a", scope="persistent", key="k", value={})

    assert len(received) == 1


async def test_delete_publishes_entry_deleted_event(bus):
    memory = build_memory_manager(event_bus=bus)
    received = []

    async def capture(event):
        received.append(event)

    await bus.subscribe(ENTRY_DELETED, capture)
    saved = await memory.save(requesting_agent_id="a", scope="persistent", key="k", value={})
    await memory.delete(requesting_agent_id="a", entry_id=saved.id)

    assert len(received) == 1


async def test_record_decision_publishes_decision_recorded_event(bus):
    memory = build_memory_manager(event_bus=bus)
    received = []

    async def capture(event):
        received.append(event)

    await bus.subscribe(DECISION_RECORDED, capture)
    await memory.record_decision(agent_id="a", summary="x")

    assert len(received) == 1


async def test_works_fully_standalone_without_bus_backend_or_vector_search(memory):
    saved = await memory.save(requesting_agent_id="a", scope="persistent", key="k", value={})
    await memory.delete(requesting_agent_id="a", entry_id=saved.id)  # no crash despite no bus/backend


async def test_backend_failure_does_not_fail_the_save_and_publishes_sync_failed(bus):
    """ObsidianVaultAdapter is a real placeholder that always raises --
    proves the backend is best-effort and never breaks the caller."""
    memory = build_memory_manager(backend=ObsidianVaultAdapter(vault_path="/tmp/fake-vault"), event_bus=bus)
    received = []

    async def capture(event):
        received.append(event)

    await bus.subscribe(BACKEND_SYNC_FAILED, capture)

    saved = await memory.save(requesting_agent_id="a", scope="persistent", key="k", value={})  # must not raise

    assert saved is not None
    assert len(received) == 1
