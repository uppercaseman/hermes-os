from hermes.modules.memory_manager.models import MemoryEntry, MemoryPermissionGrant


def test_memory_entry_defaults_to_no_owner_and_no_expiry():
    entry = MemoryEntry(scope="persistent", key="k", value={"a": 1})

    assert entry.owner_agent_id is None
    assert entry.expires_at is None
    assert entry.tags == []
    assert entry.backlinks == []


def test_permission_grant_defaults_to_read_only():
    grant = MemoryPermissionGrant(agent_id="agent-a")

    assert grant.can_read is True
    assert grant.can_write is False
    assert grant.owner_agent_id is None
