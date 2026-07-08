# Hermes Mission Control Backend

Read-only aggregated views over the Mission System. Mission
Control is the **only** module in the workspace layer permitted
to depend on Commander / Mission System (via the `MissionSource`
Protocol). It exposes:

- listings by status (running, queued, ready, paused, waiting,
  blocked, completed, failed, cancelled, archived)
- mission summaries
- mission progress
- mission timelines
- mission ownership
- mission statistics
- mission logs
- mission event streams

No frontend, no UI, no mutation. Mission Control never asks the
Mission System to do anything -- it only reads.

## Where it sits

```
  future desktop UI ──reads──> MissionControl
                                  │
                                  ▼ MissionSource (Protocol)
                            Mission System
                                  ▲
                                  │
                       (zero other downward edges anywhere)
```

## Public surface

```python
from hermes.modules.mission_control import build_mission_control

mc = build_mission_control(
    mission_source=ms,                  # any MissionSource
    event_bus=bus,                      # for timeline + live stream
)
await mc.start()
running = mc.list_running_missions()
summary = await mc.mission_summary(mid)
progress = await mc.mission_progress(mid)
timeline = mc.mission_timeline(mid)
ownership = mc.mission_ownership(mid)
stats = mc.statistics()
async for event in mc.live_event_stream():
    print(event.event_type)
```

## Models

| Type | Purpose |
| --- | --- |
| `MissionSummary` | Headline summary: id, goal, status, owner, progress %, timestamps. |
| `MissionProgress` | Aggregated progress: total/met/unmet/pending + percent. |
| `MissionTimelineEntry` | One bus event, derived from the bus log filtered by `correlation_id == mission_id`. |
| `MissionLogEntry` | One log line, also derived from the bus log (different shape than timeline). |
| `MissionStatistics` | Computed counters: total, by status, success rate, avg duration. |
| `MissionStatusGroup` | The closed set of statuses the Control understands. |

## Events

| Event | When |
| --- | --- |
| `mission_control.view.mission_summary_viewed` | After every `mission_summary` call |
| `mission_control.view.mission_timeline_viewed` | Reserved (not currently emitted; emit on call in future) |
| `mission_control.view.mission_statistics_viewed` | Reserved (not currently emitted; emit on call in future) |
| `mission_control.view.aggregate_refreshed` | Reserved (not currently emitted; emit on aggregate refresh) |
| `mission_control.view.stream_subscribed` | On entering `live_event_stream()` |
| `mission_control.view.stream_unsubscribed` | On exiting `live_event_stream()` |

## Errors

| Exception | When |
| --- | --- |
| `UnknownMissionError` | `mission_ownership` against an id the source does not know |
| `MissionControlConfigError` | Construction-time contradiction |
| `MissionControlError` | Base class |

## Backwards compatibility

- The shape of every `*Summary`, `*Progress`, `*TimelineEntry`,
  `*LogEntry`, `*Statistics` record is stable; new fields are
  additive.
- The `MissionStatusGroup` Literal is closed; new statuses
  require a coordinated update with the Mission System.

## Out of scope (future Sprints)

- Mutation: Mission Control never tells the Mission System to
  start / pause / cancel / approve a mission. A future "Mission
  Console" module would own those verbs; Mission Control stays
  read-only.
- Per-user mission filters (mine / team's / all).
- Live progress streaming (currently a snapshot).
- Mission diffing between two points in time.