import uuid
from datetime import datetime, timedelta, timezone

import pytest

from hermes.modules.logging_system.errors import UnknownLogEntryError
from hermes.modules.logging_system.interface import build_logging_system
from hermes.modules.logging_system.redaction import REDACTED
from hermes.modules.logging_system.tests.conftest import make_event


# --------------------------------------------------------------------- #
# Subscribing to the Event Bus (#1) + structured capture (#2, #3)
# --------------------------------------------------------------------- #

async def test_start_subscribes_and_captures_real_bus_events(bus):
    logger = build_logging_system(event_bus=bus)
    await logger.start()

    await bus.publish(make_event("commander.request.received", source_module="commander"))

    entries = await logger.query()
    assert len(entries) == 1
    assert entries[0].event_type == "commander.request.received"


async def test_stop_ends_capture(bus):
    logger = build_logging_system(event_bus=bus)
    await logger.start()
    await logger.stop()

    await bus.publish(make_event("commander.request.received"))

    assert await logger.query() == []


async def test_capture_preserves_correlation_id(logging_system):
    correlation_id = uuid.uuid4()

    await logging_system.capture(make_event("x.y", correlation_id=correlation_id))

    entries = await logging_system.query()
    assert entries[0].correlation_id == correlation_id


async def test_works_fully_standalone_without_an_event_bus(logging_system):
    """No bus given -- capture() still works directly."""
    await logging_system.capture(make_event("x.y"))

    assert len(await logging_system.query()) == 1


# --------------------------------------------------------------------- #
# Mission-level logs (#4)
# --------------------------------------------------------------------- #

async def test_list_by_mission_matches_explicit_payload_field(logging_system):
    mission_id = uuid.uuid4()
    await logging_system.capture(
        make_event("mission_system.mission.created", source_module="mission_system", payload={"mission_id": str(mission_id)})
    )
    await logging_system.capture(make_event("mission_system.mission.created", payload={"mission_id": str(uuid.uuid4())}))

    entries = await logging_system.list_by_mission(mission_id)

    assert len(entries) == 1


async def test_list_by_mission_also_matches_via_correlation_id_convention(logging_system):
    """Mission System sets correlation_id = mission.id on every request
    it dispatches -- everything downstream (Commander, Workflow Engine,
    Task Queue) shares that correlation_id without ever having an
    explicit mission_id field of its own."""
    mission_id = uuid.uuid4()
    await logging_system.capture(make_event("commander.request.received", source_module="commander", correlation_id=mission_id))
    await logging_system.capture(make_event("workflow_engine.run.started", source_module="workflow_engine", correlation_id=mission_id))
    await logging_system.capture(make_event("commander.request.received", correlation_id=uuid.uuid4()))  # unrelated

    entries = await logging_system.list_by_mission(mission_id)

    assert len(entries) == 2


# --------------------------------------------------------------------- #
# Workflow-level logs (#5)
# --------------------------------------------------------------------- #

async def test_list_by_workflow_run_matches_run_id_in_payload(logging_system):
    run_id = uuid.uuid4()
    await logging_system.capture(
        make_event("workflow_engine.step.completed", source_module="workflow_engine", payload={"run_id": str(run_id), "step": "a"})
    )
    await logging_system.capture(make_event("workflow_engine.step.completed", payload={"run_id": str(uuid.uuid4())}))

    entries = await logging_system.list_by_workflow_run(run_id)

    assert len(entries) == 1


# --------------------------------------------------------------------- #
# Task-level logs (#6)
# --------------------------------------------------------------------- #

async def test_list_by_task_matches_task_id_in_payload(logging_system):
    task_id = uuid.uuid4()
    await logging_system.capture(make_event("task_queue.task.claimed", payload={"task_id": str(task_id)}))
    await logging_system.capture(make_event("task_queue.task.claimed", payload={"task_id": str(uuid.uuid4())}))

    entries = await logging_system.list_by_task(task_id)

    assert len(entries) == 1


# --------------------------------------------------------------------- #
# Provider/tool logs (#7)
# --------------------------------------------------------------------- #

async def test_list_by_tool_matches_tool_name_in_payload(logging_system):
    await logging_system.capture(make_event("tool_manager.tool.invoked", payload={"tool_name": "openai"}))
    await logging_system.capture(make_event("tool_manager.tool.invoked", payload={"tool_name": "claude"}))

    entries = await logging_system.list_by_tool("openai")

    assert len(entries) == 1
    assert entries[0].tool_name == "openai"


# --------------------------------------------------------------------- #
# Error logs (#8) + health/status logs (#9)
# --------------------------------------------------------------------- #

async def test_list_errors_filters_by_inferred_severity(logging_system):
    await logging_system.capture(make_event("workflow_engine.step.failed"))
    await logging_system.capture(make_event("workflow_engine.step.completed"))

    errors = await logging_system.list_errors()

    assert len(errors) == 1
    assert errors[0].event_type == "workflow_engine.step.failed"


async def test_list_health_logs_covers_state_manager_and_supervisor(logging_system):
    await logging_system.capture(make_event("state_manager.module.state_reported", source_module="state_manager"))
    await logging_system.capture(make_event("supervisor.unit.started", source_module="supervisor"))
    await logging_system.capture(make_event("commander.request.received", source_module="commander"))

    health = await logging_system.list_health_logs()

    assert {e.source_module for e in health} == {"state_manager", "supervisor"}


# --------------------------------------------------------------------- #
# General querying (#10): module, severity, timestamp, correlation_id
# --------------------------------------------------------------------- #

async def test_query_by_source_module(logging_system):
    await logging_system.capture(make_event("x", source_module="commander"))
    await logging_system.capture(make_event("y", source_module="workflow_engine"))

    entries = await logging_system.query(source_module="commander")

    assert [e.source_module for e in entries] == ["commander"]


async def test_query_by_correlation_id(logging_system):
    cid = uuid.uuid4()
    await logging_system.capture(make_event("x", correlation_id=cid))
    await logging_system.capture(make_event("y", correlation_id=uuid.uuid4()))

    entries = await logging_system.query(correlation_id=cid)

    assert len(entries) == 1


async def test_query_by_time_range(logging_system):
    await logging_system.capture(make_event("x"))
    cutoff = datetime.now(timezone.utc) + timedelta(seconds=1)

    before = await logging_system.query(until=cutoff)
    after = await logging_system.query(since=cutoff)

    assert len(before) == 1
    assert len(after) == 0


async def test_query_combines_filters_with_and_semantics(logging_system):
    await logging_system.capture(make_event("workflow_engine.step.failed", source_module="workflow_engine"))
    await logging_system.capture(make_event("workflow_engine.step.completed", source_module="workflow_engine"))
    await logging_system.capture(make_event("commander.run.failed", source_module="commander"))

    entries = await logging_system.query(source_module="workflow_engine", severity="error")

    assert len(entries) == 1
    assert entries[0].event_type == "workflow_engine.step.failed"


async def test_get_entry_raises_for_unknown_id(logging_system):
    with pytest.raises(UnknownLogEntryError):
        await logging_system.get_entry(uuid.uuid4())


async def test_get_entry_returns_the_matching_entry(logging_system):
    await logging_system.capture(make_event("x"))
    entry = (await logging_system.query())[0]

    assert (await logging_system.get_entry(entry.id)).id == entry.id


# --------------------------------------------------------------------- #
# Replay support (#12)
# --------------------------------------------------------------------- #

async def test_replay_returns_entries_in_chronological_order(logging_system):
    cid = uuid.uuid4()
    await logging_system.capture(make_event("first", correlation_id=cid))
    await logging_system.capture(make_event("second", correlation_id=cid))
    await logging_system.capture(make_event("unrelated", correlation_id=uuid.uuid4()))

    replayed = await logging_system.replay(cid)

    assert [e.event_type for e in replayed] == ["first", "second"]


def test_render_replay_produces_a_readable_timeline(logging_system):
    from hermes.modules.logging_system.models import LogEntry

    entries = [
        LogEntry(event_type="a.b", source_module="commander", correlation_id=uuid.uuid4(), severity="info", payload={"x": 1})
    ]

    rendered = logging_system.render_replay(entries)

    assert "a.b" in rendered
    assert "commander" in rendered


# --------------------------------------------------------------------- #
# Export support (#13)
# --------------------------------------------------------------------- #

async def test_export_returns_json_serializable_dicts(logging_system):
    await logging_system.capture(make_event("x", payload={"a": 1}))

    exported = await logging_system.export()

    assert isinstance(exported, list)
    assert isinstance(exported[0]["id"], str)  # UUID serialized to str, not a UUID object
    assert isinstance(exported[0]["correlation_id"], str)


async def test_export_json_is_valid_json(logging_system):
    import json

    await logging_system.capture(make_event("x"))

    text = await logging_system.export_json()

    parsed = json.loads(text)
    assert len(parsed) == 1


async def test_export_respects_query_filters(logging_system):
    await logging_system.capture(make_event("x", source_module="commander"))
    await logging_system.capture(make_event("y", source_module="workflow_engine"))

    exported = await logging_system.export(source_module="commander")

    assert len(exported) == 1


# --------------------------------------------------------------------- #
# Redaction hooks (#14)
# --------------------------------------------------------------------- #

async def test_captured_payload_is_redacted_automatically(logging_system):
    await logging_system.capture(make_event("x", payload={"api_key": "sk-realsecretvalue123"}))

    entry = (await logging_system.query())[0]

    assert entry.payload["api_key"] == REDACTED


async def test_custom_redaction_hook_can_be_supplied():
    def custom_hook(payload: dict) -> dict:
        return {"redacted_by": "custom", **{k: v for k, v in payload.items() if k != "sensitive_field"}}

    logger = build_logging_system(redaction_hook=custom_hook)
    await logger.capture(make_event("x", payload={"sensitive_field": "secret", "keep": "this"}))

    entry = (await logger.query())[0]
    assert "sensitive_field" not in entry.payload
    assert entry.payload["redacted_by"] == "custom"
    assert entry.payload["keep"] == "this"
