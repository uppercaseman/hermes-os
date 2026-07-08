# Hermes Session Manager

The Session Manager owns one or more `WorkspaceSession` records
per Hermes login. Each session carries the current-{workspace,
application, mission, project, user} pointers and a bounded
recent-activity ring. The Manager depends on the Workspace Manager
only through the `WorkspaceAccessor` Protocol so it never imports
the Workspace Manager's concrete class.

## Where it sits

```
       future desktop UI ──reads / writes──> Session Manager
                                            │
                                            ▼ WorkspaceAccessor (Protocol)
                                       Workspace Manager
```

The Session Manager is the second-most-passive module in the
workspace layer (after Application Registry). It owns a small
amount of state, publishes events on changes, and otherwise
listens to no one.

## Public surface

```python
from hermes.modules.session_manager import build_session_manager

sm = build_session_manager(
    event_bus=bus,
    workspace_manager=ws,        # any WorkspaceAccessor
    recent_activity_capacity=50,
)

session = await sm.start_session(user_id="alice")
await sm.set_current_workspace(session.id, workspace.id)
await sm.set_current_application(session.id, "mission_control")
await sm.set_current_mission(session.id, mission_id)
recent = sm.recent_activity(session.id, limit=20)

# Persistence is explicit
await sm.persist(session.id)
restored = await sm.restore(session.id)
```

## Models

| Type | Purpose |
| --- | --- |
| `WorkspaceSession` | One session. `id`, `user_id`, five current-X pointers, recent-activity ring, `started_at`, `ended_at`. |
| `RecentActivity` | One entry: `kind`, `subject`, `timestamp`. |
| `ActivityKind` (enum) | The discrete activity kinds recorded: `session_started/ended`, `workspace/application/mission/project/user_changed`. |

## Events

| Event | When |
| --- | --- |
| `session_manager.session.started` | After `start_session` |
| `session_manager.session.ended` | After `end_session` |
| `session_manager.session.restored` | After `restore` returns a record |
| `session_manager.session.current_workspace_changed` | After `set_current_workspace` *changes* the pointer |
| `session_manager.session.current_application_changed` | After `set_current_application` *changes* the pointer |
| `session_manager.session.current_mission_changed` | After `set_current_mission` *changes* the pointer |
| `session_manager.session.current_project_changed` | After `set_current_project` *changes* the pointer |
| `session_manager.session.current_user_changed` | Reserved (future: per-session user switch); not currently emitted |

## Errors

| Exception | When |
| --- | --- |
| `UnknownSessionError` | Any method against a session id that is not active |
| `UnknownWorkspaceReferenceError` | `set_current_workspace` against an id that the `WorkspaceAccessor` does not know |
| `SessionConfigError` | Construction-time contradiction |
| `SessionManagerError` | Base class |

## Backwards compatibility

- `WorkspaceSession` field shapes are stable; new fields are additive.
- `ActivityKind` is a closed enum; new values require an additive
  migration of any consumer.
- Persistence round-trips through `WorkspaceSession.model_dump_json()`.

## Out of scope (future Sprints)

- Multi-user collaboration (multiple `user_id`s per session).
- Live session mirroring across devices.
- Per-session capability grants (today capabilities are global).
- Idle / auto-logout policies.