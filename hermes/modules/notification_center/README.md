# Hermes Notification Center

Aggregates every Hermes event into a user-facing notification
stream. Subscribes to the EventBus via the `"*"` wildcard,
classifies each incoming event by an internal severity-mapping
rule, and stores the result in a bounded ring buffer. The
Center exposes APIs to list notifications, count unread,
mark read, dismiss, and clear.

## Where it sits

```
                EventBus (any source module publishes)
                          │
                          ▼ "*" wildcard
                  NotificationCenter
                          │
                          ▼
              future desktop UI (binds NotificationSink Protocol)
```

The Center is the third-most-passive module in the workspace
layer (after Application Registry and Session Manager). It owns
a small amount of state, publishes events on changes, and
otherwise listens to the bus.

## Public surface

```python
from hermes.modules.notification_center import build_notification_center

nc = build_notification_center(event_bus=bus, history_size=500)
await nc.start()             # subscribes to "*"
notes = nc.list_notifications(severity="warning", unread_only=True)
unread = nc.unread_count()
nc.register_severity_rule("mission.failed", "error")
n = nc.raise_notification(severity="info", title="Hello")
nc.mark_read(n.id)
nc.dismiss(n.id)
cleared = nc.clear(severity="info")
```

## Severity rules

The Center ships with these defaults:

| Suffix / match | Severity |
| --- | --- |
| `*.failed`, `*.crashed`, `*.error` | `error` |
| `*.cancelled`, `*.timeout`, `*.warning` | `warning` |
| `*.completed`, `*.succeeded`, `*.saved` | `success` |
| (everything else) | `info` |

Call `register_severity_rule(prefix, severity)` to override or
add. The longest matching prefix wins. Order of evaluation is
the order of `dict` iteration -- so registering
`mission.failed -> error` after the default `failed -> error`
will not change anything because both match equally; in
practice the user-facing API is "register prefix -> severity"
and the rest is implementation detail.

## Models

| Type | Purpose |
| --- | --- |
| `Notification` | One notification. `id`, `severity`, `title`, `body`, `source_module`, `source_event_type`, `correlation_id`, `is_read`, `is_dismissed`, `created_at`. |
| `NotificationAggregate` | Computed rollup: `total`, `unread`, `by_severity`, `unread_by_severity`. |
| `Severity` (Literal) | `"info"`, `"success"`, `"warning"`, `"error"`, `"critical"`. |

## Events

| Event | When |
| --- | --- |
| `notification_center.notification.raised` | After a new notification is stored |
| `notification_center.notification.read` | After `mark_read` |
| `notification_center.notification.dismissed` | After `dismiss` |
| `notification_center.notification.cleared` | After `clear` (one event per call, payload carries count) |
| `notification_center.filter.changed` | After `register_severity_rule` |

## Errors

| Exception | When |
| --- | --- |
| `UnknownNotificationError` | `mark_read` / `dismiss` against an id that is not in history |
| `NotificationCenterConfigError` | Construction-time contradiction |
| `NotificationCenterError` | Base class |

## Backwards compatibility

- `Notification` field shapes are stable.
- Default severity rules are stable. New defaults are additive;
  existing prefixes are never reclassified.
- `history_size` is a default; bumping it across versions is
  backward-compatible.

## Out of scope (future Sprints)

- Push delivery to a desktop notification daemon.
- Per-user notification preferences.
- Rich text / markdown bodies.
- Notification templating.