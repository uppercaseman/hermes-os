import asyncio
import inspect
import uuid

import pytest

from hermes.core.event_bus.models import Event
from hermes.core.state_manager.errors import UnknownModuleError
from hermes.core.state_manager.events import RECOVERY_EXHAUSTED, RESTART_REQUESTED, STATE_REPORTED
from hermes.core.state_manager.interface import build_state_manager
from hermes.core.supervisor import events as supervisor_events
from hermes.core.supervisor.interface import build_supervisor
from hermes.core.supervisor.models import SupervisedUnitConfig
from hermes.core.supervisor.policy import RetryPolicy
from hermes.core.supervisor.tests.fakes import ScriptedUnit

FAST = 0.02


def _supervisor_event(event_type: str, unit: str) -> Event:
    return Event(event_type=event_type, source_module="supervisor", correlation_id=uuid.uuid4(), payload={"unit": unit})


async def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> None:
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return
        await asyncio.sleep(interval)
        elapsed += interval
    raise AssertionError("condition not met within timeout")


# --------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------- #

def test_get_state_raises_for_a_truly_unknown_module(state_manager):
    with pytest.raises(UnknownModuleError):
        state_manager.get_state("nope")


def test_declared_but_never_heartbeat_module_reads_as_offline(state_manager):
    state_manager.declare_module("memory_manager")

    assert state_manager.get_state("memory_manager") == "offline"


@pytest.mark.parametrize(
    "state", ["healthy", "busy", "idle", "offline", "restarting", "failed", "degraded"]
)
async def test_report_heartbeat_is_reflected_by_get_state(state_manager, state):
    await state_manager.report_heartbeat("tool_manager", state)

    assert state_manager.get_state("tool_manager") == state


def test_query_methods_are_synchronous_by_design(state_manager):
    """Architectural guard for "Commander must be able to query every
    module at any time": these must never be coroutines a caller could
    forget to await or that could be blocked waiting on something else."""
    assert not inspect.iscoroutinefunction(state_manager.get_state)
    assert not inspect.iscoroutinefunction(state_manager.get_state_all)
    assert not inspect.iscoroutinefunction(state_manager.diagnostics)
    assert not inspect.iscoroutinefunction(state_manager.diagnostics_all)


async def test_get_state_all_covers_every_known_module(state_manager):
    await state_manager.report_heartbeat("a", "healthy")
    await state_manager.report_heartbeat("b", "busy")

    assert state_manager.get_state_all() == {"a": "healthy", "b": "busy"}


# --------------------------------------------------------------------- #
# Dependency tracking
# --------------------------------------------------------------------- #

async def test_dependency_failure_degrades_a_healthy_dependent(state_manager):
    state_manager.declare_module("workflow_engine", depends_on=["memory_manager"])
    await state_manager.report_heartbeat("workflow_engine", "healthy")
    await state_manager.report_heartbeat("memory_manager", "failed")

    assert state_manager.get_state("workflow_engine") == "degraded"
    diagnostics = state_manager.diagnostics("workflow_engine")
    assert diagnostics.reported_state == "healthy"  # raw report is untouched
    assert diagnostics.effective_state == "degraded"
    assert diagnostics.unmet_dependencies == ["memory_manager"]


async def test_healthy_dependency_does_not_degrade_anything(state_manager):
    state_manager.declare_module("workflow_engine", depends_on=["memory_manager"])
    await state_manager.report_heartbeat("workflow_engine", "healthy")
    await state_manager.report_heartbeat("memory_manager", "healthy")

    assert state_manager.get_state("workflow_engine") == "healthy"
    assert state_manager.diagnostics("workflow_engine").unmet_dependencies == []


async def test_already_failed_module_is_not_further_masked_as_degraded(state_manager):
    state_manager.declare_module("workflow_engine", depends_on=["memory_manager"])
    await state_manager.report_heartbeat("workflow_engine", "failed")
    await state_manager.report_heartbeat("memory_manager", "failed")

    assert state_manager.get_state("workflow_engine") == "failed"  # its own failure is the more specific signal


async def test_cyclic_dependency_declaration_does_not_recurse_forever(state_manager):
    state_manager.declare_module("a", depends_on=["b"])
    state_manager.declare_module("b", depends_on=["a"])
    await state_manager.report_heartbeat("a", "healthy")
    await state_manager.report_heartbeat("b", "failed")

    assert state_manager.get_state("a") == "degraded"
    assert state_manager.get_state("b") == "failed"


# --------------------------------------------------------------------- #
# Restart requests
# --------------------------------------------------------------------- #

async def test_restart_request_without_a_supervisor_is_recorded_pending(state_manager):
    request = await state_manager.request_restart("tool_manager", reason="manual", requested_by="operator")

    assert request.status == "pending"
    assert state_manager.get_state("tool_manager") == "restarting"


async def test_restart_request_for_unregistered_supervisor_unit_fails_gracefully(bus):
    supervisor = build_supervisor(event_bus=bus)
    manager = build_state_manager(supervisor=supervisor)

    request = await manager.request_restart("never-registered", reason="test")

    assert request.status == "failed"


async def test_restart_request_with_a_supervisor_actually_restarts_the_unit(bus):
    supervisor = build_supervisor(event_bus=bus)
    unit = ScriptedUnit()
    supervisor.register(unit, SupervisedUnitConfig(name="tool_manager", health_check_interval_seconds=FAST))
    await supervisor.start_all()

    manager = build_state_manager(supervisor=supervisor)
    request = await manager.request_restart("tool_manager", reason="operator request", requested_by="operator")

    assert request.status == "completed"
    assert unit.stop_calls == 1
    assert unit.start_calls == 2  # initial start + the restart
    supervisor_status = await supervisor.status("tool_manager")
    assert supervisor_status.state == "running"

    await supervisor.stop_all()


async def test_diagnostics_tracks_restart_history(state_manager):
    await state_manager.request_restart("tool_manager", reason="first")
    await state_manager.request_restart("tool_manager", reason="second")

    diagnostics = state_manager.diagnostics("tool_manager")
    assert diagnostics.restart_count == 2
    assert diagnostics.last_restart_reason == "second"


# --------------------------------------------------------------------- #
# System-wide diagnostics rollup
# --------------------------------------------------------------------- #

async def test_diagnostics_all_rolls_up_to_healthy_when_everything_is_fine(state_manager):
    await state_manager.report_heartbeat("a", "healthy")
    await state_manager.report_heartbeat("b", "idle")

    assert state_manager.diagnostics_all().overall_state == "healthy"


async def test_diagnostics_all_rolls_up_to_degraded(state_manager):
    await state_manager.report_heartbeat("a", "healthy")
    await state_manager.report_heartbeat("b", "offline")

    assert state_manager.diagnostics_all().overall_state == "degraded"


async def test_diagnostics_all_rolls_up_to_critical_when_anything_failed(state_manager):
    await state_manager.report_heartbeat("a", "healthy")
    await state_manager.report_heartbeat("b", "failed")

    assert state_manager.diagnostics_all().overall_state == "critical"


# --------------------------------------------------------------------- #
# Heartbeat staleness + automatic recovery
# --------------------------------------------------------------------- #

async def test_stale_active_heartbeat_is_marked_offline_and_triggers_recovery(bus):
    """Auto-recovery fires in the same sweep pass that detects staleness,
    so by the time it's observable the module has already moved past
    "offline" into "restarting" -- assert on that stable end state and on
    the recovery having actually been recorded, not on catching the
    transient "offline" moment (which would be a race)."""
    manager = build_state_manager(
        event_bus=bus,
        heartbeat_timeout_seconds=FAST,
        sweep_interval_seconds=FAST,
        recovery_policy=RetryPolicy(max_attempts=3, backoff_base_seconds=0, backoff_multiplier=1),
    )
    await manager.report_heartbeat("tool_manager", "healthy")
    await manager.start()

    await _wait_until(lambda: manager.diagnostics("tool_manager").restart_count >= 1, timeout=1.0)

    diagnostics = manager.diagnostics("tool_manager")
    assert diagnostics.last_restart_reason == "heartbeat timeout"
    assert manager.get_state("tool_manager") == "restarting"  # auto-recovery already in flight

    await manager.stop()


async def test_passive_supervisor_derived_modules_are_exempt_from_staleness(bus):
    manager = build_state_manager(event_bus=bus, heartbeat_timeout_seconds=FAST, sweep_interval_seconds=FAST)
    await manager.start()

    await bus.publish(_supervisor_event(supervisor_events.UNIT_STARTED, "openai"))
    await asyncio.sleep(FAST * 5)  # well past the "timeout" if staleness applied

    assert manager.get_state("openai") == "healthy"  # never flipped to offline
    await manager.stop()


# --------------------------------------------------------------------- #
# Supervisor event translation
# --------------------------------------------------------------------- #

async def test_unit_started_event_reports_healthy(bus):
    manager = build_state_manager(event_bus=bus)
    await manager.start()

    await bus.publish(_supervisor_event(supervisor_events.UNIT_STARTED, "openai"))

    assert manager.get_state("openai") == "healthy"
    await manager.stop()


async def test_unit_crashed_reports_restarting_without_triggering_state_manager_recovery(bus):
    """Supervisor is already handling a plain crash -- State Manager
    should reflect that, not launch a competing recovery attempt."""
    manager = build_state_manager(event_bus=bus)
    await manager.start()

    await bus.publish(_supervisor_event(supervisor_events.UNIT_CRASHED, "openai"))

    assert manager.get_state("openai") == "restarting"
    assert manager.diagnostics("openai").restart_count == 0
    await manager.stop()


async def test_restart_exhausted_reports_failed_and_triggers_state_manager_recovery(bus):
    manager = build_state_manager(
        event_bus=bus, recovery_policy=RetryPolicy(max_attempts=3, backoff_base_seconds=0, backoff_multiplier=1)
    )
    await manager.start()

    await bus.publish(_supervisor_event(supervisor_events.UNIT_RESTART_EXHAUSTED, "openai"))
    await _wait_until(lambda: manager.diagnostics("openai").restart_count == 1)

    diagnostics = manager.diagnostics("openai")
    assert diagnostics.last_restart_reason == f"supervisor: {supervisor_events.UNIT_RESTART_EXHAUSTED}"
    await manager.stop()


async def test_auto_recover_false_suppresses_automatic_restart_requests(bus):
    manager = build_state_manager(event_bus=bus)
    manager.declare_module("openai", auto_recover=False)
    await manager.start()

    await bus.publish(_supervisor_event(supervisor_events.UNIT_RESTART_EXHAUSTED, "openai"))
    await asyncio.sleep(FAST * 2)

    assert manager.get_state("openai") == "failed"
    assert manager.diagnostics("openai").restart_count == 0
    await manager.stop()


async def test_recovery_attempts_are_bounded_and_publish_exhaustion_event(bus):
    manager = build_state_manager(
        event_bus=bus, recovery_policy=RetryPolicy(max_attempts=2, backoff_base_seconds=0, backoff_multiplier=1)
    )
    exhausted = []

    async def capture(event):
        exhausted.append(event)

    await bus.subscribe(RECOVERY_EXHAUSTED, capture)
    await manager.start()

    for _ in range(4):  # far more failures than max_attempts allows
        await bus.publish(_supervisor_event(supervisor_events.UNIT_RESTART_EXHAUSTED, "openai"))
        await asyncio.sleep(0.01)

    assert manager.diagnostics("openai").restart_count == 1  # bounded, not one per event
    assert len(exhausted) >= 1
    await manager.stop()


# --------------------------------------------------------------------- #
# Lifecycle + event publishing
# --------------------------------------------------------------------- #

async def test_stop_prevents_further_automatic_state_updates(bus):
    manager = build_state_manager(event_bus=bus)
    await manager.start()
    await manager.stop()

    await bus.publish(_supervisor_event(supervisor_events.UNIT_STARTED, "openai"))

    with pytest.raises(UnknownModuleError):
        manager.get_state("openai")  # never observed -- stop() was effective


async def test_report_heartbeat_publishes_state_reported_event(bus):
    manager = build_state_manager(event_bus=bus)
    received = []

    async def capture(event):
        received.append(event)

    await bus.subscribe(STATE_REPORTED, capture)
    await manager.report_heartbeat("tool_manager", "busy")

    assert len(received) == 1
    assert received[0].payload["state"] == "busy"


async def test_request_restart_publishes_restart_requested_event(bus):
    manager = build_state_manager(event_bus=bus)
    received = []

    async def capture(event):
        received.append(event)

    await bus.subscribe(RESTART_REQUESTED, capture)
    await manager.request_restart("tool_manager", reason="test", requested_by="operator")

    assert len(received) == 1
    assert received[0].payload["requested_by"] == "operator"


async def test_works_fully_standalone_without_an_event_bus(state_manager):
    """No bus given -- every method must still work; publishing is a
    no-op. (state_manager fixture has no bus.)"""
    await state_manager.report_heartbeat("a", "healthy")
    await state_manager.request_restart("a", reason="test")

    assert state_manager.get_state("a") == "restarting"
