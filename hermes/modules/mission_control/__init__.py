"""Mission Control Backend -- aggregated views over the Mission System.

Mission Control reads from a `MissionSource` Protocol (a narrow
shape that the real `MissionSystem` satisfies implicitly), and
exposes APIs for every aggregated view the future desktop UI
needs:

- listing by status (running, queued, ready, paused, waiting,
  blocked, completed, failed, cancelled, archived)
- mission summaries
- mission progress
- mission timelines
- mission ownership
- mission statistics
- mission logs
- mission event streams

Mission Control NEVER writes to the Mission System -- it is
read-only. The only downward edge in the entire workspace layer.
"""
from hermes.modules.mission_control.interface import build_mission_control
from hermes.modules.mission_control.service import MissionControl

__all__ = ["MissionControl", "build_mission_control"]