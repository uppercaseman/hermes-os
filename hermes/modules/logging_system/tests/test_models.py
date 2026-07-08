import uuid

from hermes.modules.logging_system.models import LogEntry


def test_log_entry_defaults():
    entry = LogEntry(event_type="x.y.z", source_module="test", correlation_id=uuid.uuid4(), severity="info")

    assert entry.payload == {}
    assert entry.mission_id is None
    assert entry.workflow_run_id is None
    assert entry.task_id is None
    assert entry.tool_name is None
