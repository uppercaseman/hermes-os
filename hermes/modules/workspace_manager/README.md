# Hermes Workspace Manager

The Workspace Manager owns the user's workspaces. Each workspace
carries an identity, owner, current application, list of open
mission ids, list of open project ids, the set of open
application ids, an optional `LayoutState` (windows + docks),
and timestamps. The Manager owns a single "current workspace"
pointer and exposes APIs to create / query / focus / close
workspaces; persist them via a pluggable `WorkspaceStore`
Protocol; and snapshot a `LayoutState` onto a workspace.

## Where it sits

```
        Session Manager ───reads / writes──> Workspace Manager
        Mission Control ───reads──> Workspace Manager
        future desktop UI ───reads / writes──> Workspace Manager
                       \                        /
                        ──> ApplicationRegistry <── (Protocol only)
```

The Manager is the only workspace-layer module that imports a
sibling workspace-layer module's runtime surface; everything
outside the workspace layer reads through `WorkspaceManagerProtocol`.

## Public surface

```python
from hermes.modules.workspace_manager import build_workspace_manager

ws = build_workspace_manager(
    event_bus=bus,
    application_registry=registry,
    store=None,         # defaults to InMemoryWorkspaceStore
)

workspace = await ws.create_workspace(name="Default", owner="alice")
await ws.set_current_workspace(workspace.id)
await ws.set_current_application(workspace.id, "mission_control")
await ws.open_mission(workspace.id, mission_id)
ws.snapshot_layout(workspace.id, LayoutState(workspace_id=workspace.id, ...))
state = ws.get_layout_state(workspace.id)
await ws.save_workspace(workspace.id)        # uses store.save
restored = await ws.restore_workspace(ws_id)  # uses store.load
```

## Models

| Type | Purpose |
| --- | --- |
| `Workspace` | Top-level record. Identity, owner, `current_application_id`, `open_mission_ids`, `open_project_ids`, `open_application_ids`, optional `layout`, timestamps. |
| `LayoutState` | Aggregated layout for one workspace. Lists of `WindowState` and `DockingState`. |
| `WindowState` | One open window. `window_id`, `application_id`, `title`, `bounds` (free-form dict), focus / minimized / maximized flags. |
| `DockingState` | One dock. `name`, `region`, list of docked `window_id`s. |

## Persistence Protocol

`WorkspaceStore` is a `runtime_checkable` Protocol with three
async methods: `save(workspace)`, `load(id) -> Workspace | None`,
and `list_ids() -> list[UUID]`. The Manager ships two
implementations:

| Class | Used by |
| --- | --- |
| `InMemoryWorkspaceStore` | tests; ephemeral runs |
| `JsonFileWorkspaceStore` | future desktop UI's startup path; writes `~/.hermes/workspaces/<uuid>.json` |

A future `PostgresWorkspaceStore`, `SqliteWorkspaceStore`, or
cloud-KV `WorkspaceStore` plugs in without changing anything
else -- the Manager calls only Protocol methods.

## Events

| Event | When |
| --- | --- |
| `workspace_manager.workspace.created` | After `create_workspace` |
| `workspace_manager.workspace.opened` | After `restore_workspace` returns a record |
| `workspace_manager.workspace.closed` | After `delete_workspace` |
| `workspace_manager.workspace.focused` | After `set_current_workspace` *changes* the pointer |
| `workspace_manager.workspace.saved` | After `save_workspace` |
| `workspace_manager.workspace.mission_opened` | After `open_mission` adds a mission id |
| `workspace_manager.workspace.mission_closed` | After `close_mission` removes a mission id |
| `workspace_manager.layout.changed` | After `set_current_application` or `snapshot_layout` |

## Errors

| Exception | When |
| --- | --- |
| `UnknownWorkspaceError` | Read/write/delete/restore against an id that has no in-memory record |
| `WorkspaceConfigError` | Construction-time contradiction |
| `ApplicationNotFoundError` (re-used) | `set_current_application` against an id that the `ApplicationRegistry` does not know |

## Backwards compatibility

- `Workspace` field shapes are stable; new fields are additive.
- `LayoutState` / `WindowState` / `DockingState` are stable.
- `WorkspaceStore` is a Protocol; the disk format on JSON
  serialization is "the `Workspace` model_dump_json()" -- any
  reader using the same Pydantic class can round-trip.

## Out of scope (future Sprints)

- Multi-user workspaces with permissions per workspace.
- The actual desktop UI; this module is metadata + state only.
- Cooperative layout editing (a Yjs/CRDT layer over `LayoutState`).
- Workspace templates (saving a workspace as a reusable template).
