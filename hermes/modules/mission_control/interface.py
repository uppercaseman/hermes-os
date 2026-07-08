"""Public entry point for Mission Control."""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.modules.mission_control.contracts import MissionSource
from hermes.modules.mission_control.service import MissionControl

__all__ = ["MissionControl", "build_mission_control"]


def build_mission_control(
    *,
    mission_source: MissionSource,
    event_bus: EventBus | None = None,
    recent_event_buffer_size: int = 1024,
) -> MissionControl:
    """Constructs a MissionControl.

    `mission_source` is required (a `MissionSource` Protocol; the
    real Mission System satisfies it). `event_bus` is optional;
    when absent, the `live_event_stream()` iterator yields nothing
    and `event_bus`-publishing paths silently skip. `recent_event_buffer_size`
    bounds the in-memory event archive the Control maintains for
    timeline reconstruction.
    """
    return MissionControl(
        mission_source=mission_source,
        event_bus=event_bus,
        recent_event_buffer_size=recent_event_buffer_size,
    )