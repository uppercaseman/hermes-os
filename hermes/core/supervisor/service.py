"""Supervisor -- manages module lifecycle, health monitoring, and
automatic restart.

This is the missing half of the supervisor tree described in the
architecture doc. `RetryPolicy` (policy.py) already provided the backoff
math for retrying a failed *task*; this file provides the thing that
starts, stops, health-checks, and restarts a *module* using that same
math, closing the "Commander supervises the other modules" gap called out
in the architecture review.

Design notes (see contracts.py for the Supervisable protocol itself):

- A unit's `start()` returning successfully means "running", not
  "finished" -- Hermes modules are event-driven, not perpetual loops.
  Liveness after that point is judged by a periodic `health_check()` poll,
  run as one background asyncio.Task per unit.
- A restart replays the exact same startup path (`_start_unit`), so a
  restarted unit is monitored exactly like a freshly-started one.
- Restart decisions reuse `RetryPolicy` for backoff, and additionally
  consult the unit's `RestartStrategy` (permanent/transient/temporary) to
  decide whether a given failure should be retried at all.
- Every lifecycle transition is published to the event bus before or as it
  happens (`supervisor.unit.*`), for the same reason Commander does this:
  a supervised system's history should be reconstructable from the log.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.core.supervisor import events as evt
from hermes.core.supervisor.contracts import Supervisable
from hermes.core.supervisor.models import SupervisedUnitConfig, UnitStatus

logger = logging.getLogger(__name__)

SOURCE_MODULE = "supervisor"


@dataclass
class _UnitRecord:
    """Private runtime bookkeeping for one registered unit. Never exposed
    outside the Supervisor -- callers only ever see `UnitStatus`."""

    unit: Supervisable
    config: SupervisedUnitConfig
    status: UnitStatus
    health_task: asyncio.Task[None] | None = None
    stopping: bool = False


class Supervisor:
    """Owns the lifecycle of every registered module: starting it,
    periodically checking its health, and restarting it per its
    configured strategy when it crashes or reports unhealthy."""

    def __init__(self, *, event_bus: EventBus) -> None:
        self._bus = event_bus
        self._units: dict[str, _UnitRecord] = {}

    def register(self, unit: Supervisable, config: SupervisedUnitConfig) -> None:
        """Registers a unit to be supervised under `config.name`. Does not
        start it -- call `start_all()` or `start(name)` once every unit
        you want supervised together has been registered.

        Raises `ValueError` if `config.name` is already registered.
        """
        if config.name in self._units:
            raise ValueError(f"a unit named {config.name!r} is already registered")
        self._units[config.name] = _UnitRecord(
            unit=unit,
            config=config,
            status=UnitStatus(name=config.name, state="stopped"),
        )

    async def start_all(self) -> None:
        """Starts every registered unit concurrently."""
        await asyncio.gather(*(self.start(name) for name in self._units))

    async def start(self, name: str) -> None:
        """Starts one registered unit and begins its health-check loop.
        If `start()` raises, the failure is handled exactly like a
        later crash -- see `_handle_failure`."""
        record = self._require(name)
        record.stopping = False
        await self._start_unit(record)

    async def stop_all(self) -> None:
        """Stops every registered unit and cancels its health-check loop.
        Never triggers a restart, regardless of restart strategy."""
        await asyncio.gather(*(self.stop(name) for name in self._units))

    async def stop(self, name: str) -> None:
        """Stops one registered unit. Safe to call on a unit that was
        never started or is mid-restart-backoff."""
        record = self._require(name)
        record.stopping = True
        if record.health_task is not None:
            record.health_task.cancel()
            record.health_task = None
        try:
            await record.unit.stop()
        except Exception:  # noqa: BLE001 -- a broken stop() must not stop
            # the supervisor from marking the unit stopped and moving on.
            logger.exception("stop() raised for unit=%s", name)
        record.status = record.status.model_copy(update={"state": "stopped"})
        await self._publish(evt.UNIT_STOPPED, name, {})

    async def status(self, name: str) -> UnitStatus:
        """Returns the current observable status of one registered unit."""
        return self._require(name).status

    async def status_all(self) -> list[UnitStatus]:
        """Returns the current observable status of every registered
        unit."""
        return [record.status for record in self._units.values()]

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _start_unit(self, record: _UnitRecord) -> None:
        record.status = record.status.model_copy(update={"state": "starting"})
        await self._publish(evt.UNIT_STARTING, record.config.name, {})
        try:
            await record.unit.start()
        except Exception as exc:  # noqa: BLE001 -- a startup crash is data
            # for the restart policy to react to, not a reason to crash
            # the Supervisor itself.
            await self._handle_failure(record, exc, was_crash=True)
            return

        record.status = record.status.model_copy(update={"state": "running"})
        await self._publish(evt.UNIT_STARTED, record.config.name, {})
        record.health_task = asyncio.ensure_future(self._health_check_loop(record))

    async def _health_check_loop(self, record: _UnitRecord) -> None:
        # `consecutive_failures` is deliberately NOT reset just because a
        # restart's `start()` call succeeded -- that only proves the unit
        # came back up, not that it's stable. It resets here, the first
        # time a post-restart health check actually reports healthy, which
        # is what lets a unit that keeps crash-looping (start succeeds
        # every time, health_check never does) still exhaust its retries
        # instead of restarting forever.
        interval = record.config.health_check_interval_seconds
        try:
            while True:
                await asyncio.sleep(interval)
                if record.stopping:
                    return
                try:
                    healthy = await record.unit.health_check()
                except Exception as exc:  # noqa: BLE001 -- treated as a crash
                    await self._handle_failure(record, exc, was_crash=True)
                    return
                if not healthy:
                    await self._handle_failure(
                        record, RuntimeError("health_check reported unhealthy"), was_crash=False
                    )
                    return
                if record.status.consecutive_failures:
                    record.status = record.status.model_copy(update={"consecutive_failures": 0})
        except asyncio.CancelledError:
            return

    async def _handle_failure(self, record: _UnitRecord, error: Exception, *, was_crash: bool) -> None:
        if record.stopping:
            return  # a deliberate stop() is not a failure to react to

        consecutive_failures = record.status.consecutive_failures + 1
        record.status = record.status.model_copy(
            update={"consecutive_failures": consecutive_failures, "last_error": str(error)}
        )
        await self._publish(
            evt.UNIT_CRASHED if was_crash else evt.UNIT_UNHEALTHY,
            record.config.name,
            {"error": str(error), "consecutive_failures": consecutive_failures},
        )

        if not self._should_restart(record.config, was_crash=was_crash):
            record.status = record.status.model_copy(update={"state": "failed"})
            await self._publish(
                evt.UNIT_RESTART_SKIPPED,
                record.config.name,
                {"restart_strategy": record.config.restart_strategy},
            )
            return

        policy = record.config.retry_policy
        if not policy.should_retry(consecutive_failures, policy.max_attempts):
            record.status = record.status.model_copy(update={"state": "failed"})
            await self._publish(
                evt.UNIT_RESTART_EXHAUSTED,
                record.config.name,
                {"consecutive_failures": consecutive_failures},
            )
            return

        backoff = policy.next_backoff(consecutive_failures)
        record.status = record.status.model_copy(
            update={"state": "restarting", "restart_count": record.status.restart_count + 1}
        )
        await self._publish(
            evt.UNIT_RESTARTING,
            record.config.name,
            {"attempt": consecutive_failures, "backoff_seconds": backoff},
        )
        if backoff > 0:
            await asyncio.sleep(backoff)
        if record.stopping:
            return
        await self._start_unit(record)

    @staticmethod
    def _should_restart(config: SupervisedUnitConfig, *, was_crash: bool) -> bool:
        if config.restart_strategy == "temporary":
            return False
        if config.restart_strategy == "transient":
            return was_crash
        return True  # permanent

    def _require(self, name: str) -> _UnitRecord:
        if name not in self._units:
            raise KeyError(f"no unit named {name!r} is registered")
        return self._units[name]

    async def _publish(self, event_type: str, unit_name: str, payload: dict[str, Any]) -> None:
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module=SOURCE_MODULE,
                correlation_id=uuid.uuid4(),
                payload={"unit": unit_name, **payload},
            )
        )
