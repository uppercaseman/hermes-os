import asyncio

import pytest

from hermes.core.supervisor.events import (
    UNIT_RESTART_EXHAUSTED,
    UNIT_RESTART_SKIPPED,
    UNIT_RESTARTING,
)
from hermes.core.supervisor.interface import build_supervisor
from hermes.core.supervisor.models import SupervisedUnitConfig
from hermes.core.supervisor.policy import RetryPolicy
from hermes.core.supervisor.tests.fakes import ScriptedUnit

FAST = 0.02  # health-check interval used throughout -- keeps tests quick
INSTANT_RETRY = RetryPolicy(max_attempts=3, backoff_base_seconds=0, backoff_multiplier=1)


async def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> None:
    """Polls `predicate` until it's true, instead of a fixed sleep -- keeps
    tests both fast and non-flaky regardless of scheduler timing."""
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return
        await asyncio.sleep(interval)
        elapsed += interval
    raise AssertionError("condition not met within timeout")


async def test_start_all_marks_every_unit_running(bus):
    supervisor = build_supervisor(event_bus=bus)
    unit = ScriptedUnit()
    supervisor.register(unit, SupervisedUnitConfig(name="unit-a", health_check_interval_seconds=FAST))

    await supervisor.start_all()

    status = await supervisor.status("unit-a")
    assert status.state == "running"
    assert unit.start_calls == 1

    await supervisor.stop_all()


async def test_registering_duplicate_name_raises(bus):
    supervisor = build_supervisor(event_bus=bus)
    supervisor.register(ScriptedUnit(), SupervisedUnitConfig(name="dup"))

    with pytest.raises(ValueError):
        supervisor.register(ScriptedUnit(), SupervisedUnitConfig(name="dup"))


async def test_status_of_unknown_unit_raises_key_error(bus):
    supervisor = build_supervisor(event_bus=bus)

    with pytest.raises(KeyError):
        await supervisor.status("nope")


async def test_permanent_strategy_restarts_after_crash_and_recovers(bus):
    unit = ScriptedUnit(start_outcomes=["ok"], health_outcomes=["raise", "ok"])
    supervisor = build_supervisor(event_bus=bus)
    supervisor.register(
        unit,
        SupervisedUnitConfig(
            name="flaky",
            restart_strategy="permanent",
            retry_policy=INSTANT_RETRY,
            health_check_interval_seconds=FAST,
        ),
    )

    await supervisor.start_all()
    await _wait_until(lambda: unit.start_calls >= 2)  # crashed once, restarted

    status = await supervisor.status("flaky")
    assert status.state in ("running", "restarting")
    assert status.restart_count >= 1

    await supervisor.stop_all()


async def test_temporary_strategy_never_restarts_on_crash(bus):
    unit = ScriptedUnit(start_outcomes=["ok"], health_outcomes=["raise"])
    supervisor = build_supervisor(event_bus=bus)
    supervisor.register(
        unit,
        SupervisedUnitConfig(
            name="one-shot",
            restart_strategy="temporary",
            retry_policy=INSTANT_RETRY,
            health_check_interval_seconds=FAST,
        ),
    )

    skipped: list = []

    async def capture(event):
        skipped.append(event)

    await bus.subscribe(UNIT_RESTART_SKIPPED, capture)
    await supervisor.start_all()
    await _wait_until(lambda: len(skipped) >= 1)
    await asyncio.sleep(FAST * 3)  # give it a chance to (incorrectly) restart

    assert unit.start_calls == 1
    status = await supervisor.status("one-shot")
    assert status.state == "failed"


async def test_transient_strategy_restarts_on_crash_but_not_on_plain_unhealthy(bus):
    crash_unit = ScriptedUnit(start_outcomes=["ok"], health_outcomes=["raise"])
    unhealthy_unit = ScriptedUnit(start_outcomes=["ok"], health_outcomes=["unhealthy"])
    supervisor = build_supervisor(event_bus=bus)
    config = SupervisedUnitConfig(
        name="crash-unit",
        restart_strategy="transient",
        retry_policy=INSTANT_RETRY,
        health_check_interval_seconds=FAST,
    )
    supervisor.register(crash_unit, config)
    supervisor.register(
        unhealthy_unit,
        config.model_copy(update={"name": "unhealthy-unit"}),
    )

    await supervisor.start_all()
    await _wait_until(lambda: crash_unit.start_calls >= 2)  # crash -> restarted
    await asyncio.sleep(FAST * 3)  # give the unhealthy unit the same window

    assert unhealthy_unit.start_calls == 1  # never restarted for plain unhealthy
    unhealthy_status = await supervisor.status("unhealthy-unit")
    assert unhealthy_status.state == "failed"

    await supervisor.stop_all()


async def test_restart_exhausted_after_max_attempts_marks_failed(bus):
    unit = ScriptedUnit(start_outcomes=["ok"], health_outcomes=["raise"])  # always crashes
    supervisor = build_supervisor(event_bus=bus)
    supervisor.register(
        unit,
        SupervisedUnitConfig(
            name="always-crashes",
            restart_strategy="permanent",
            retry_policy=RetryPolicy(max_attempts=2, backoff_base_seconds=0, backoff_multiplier=1),
            health_check_interval_seconds=FAST,
        ),
    )

    exhausted: list = []

    async def capture(event):
        exhausted.append(event)

    await bus.subscribe(UNIT_RESTART_EXHAUSTED, capture)
    await supervisor.start_all()
    await _wait_until(lambda: len(exhausted) >= 1)

    status = await supervisor.status("always-crashes")
    assert status.state == "failed"


async def test_restarting_event_carries_backoff_seconds(bus):
    unit = ScriptedUnit(start_outcomes=["ok"], health_outcomes=["raise", "ok"])
    supervisor = build_supervisor(event_bus=bus)
    supervisor.register(
        unit,
        SupervisedUnitConfig(
            name="backoff-unit",
            retry_policy=RetryPolicy(max_attempts=3, backoff_base_seconds=0, backoff_multiplier=1),
            health_check_interval_seconds=FAST,
        ),
    )

    restarting_events: list = []

    async def capture(event):
        restarting_events.append(event)

    await bus.subscribe(UNIT_RESTARTING, capture)
    await supervisor.start_all()
    await _wait_until(lambda: len(restarting_events) >= 1)

    assert "backoff_seconds" in restarting_events[0].payload
    await supervisor.stop_all()


async def test_stop_prevents_restart_and_cancels_health_loop(bus):
    unit = ScriptedUnit(start_outcomes=["ok"], health_outcomes=["ok"])
    supervisor = build_supervisor(event_bus=bus)
    supervisor.register(unit, SupervisedUnitConfig(name="stoppable", health_check_interval_seconds=FAST))

    await supervisor.start_all()
    await supervisor.stop_all()

    calls_at_stop = unit.health_calls
    await asyncio.sleep(FAST * 5)

    assert unit.stop_calls == 1
    assert unit.health_calls <= calls_at_stop  # loop was actually cancelled
    status = await supervisor.status("stoppable")
    assert status.state == "stopped"


async def test_status_all_reports_every_registered_unit(bus):
    supervisor = build_supervisor(event_bus=bus)
    supervisor.register(ScriptedUnit(), SupervisedUnitConfig(name="a", health_check_interval_seconds=FAST))
    supervisor.register(ScriptedUnit(), SupervisedUnitConfig(name="b", health_check_interval_seconds=FAST))

    await supervisor.start_all()
    statuses = await supervisor.status_all()

    assert {s.name for s in statuses} == {"a", "b"}
    assert all(s.state == "running" for s in statuses)

    await supervisor.stop_all()


async def test_crash_during_startup_is_handled_like_any_other_failure(bus):
    unit = ScriptedUnit(start_outcomes=["raise", "raise", "ok"])
    supervisor = build_supervisor(event_bus=bus)
    supervisor.register(
        unit,
        SupervisedUnitConfig(
            name="slow-starter",
            retry_policy=INSTANT_RETRY,
            health_check_interval_seconds=FAST,
        ),
    )

    await supervisor.start_all()
    await _wait_until(lambda: unit.start_calls >= 3)

    status = await supervisor.status("slow-starter")
    assert status.state == "running"

    await supervisor.stop_all()
