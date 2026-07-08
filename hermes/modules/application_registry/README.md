# Hermes Application Registry

The single source of truth for "which apps exist in Hermes today."

The Application Registry is the most-passive module in the
workspace layer: a thin, deterministic catalog of `Application`
records. It owns no runtime state, performs no I/O, and is read
by every other workspace module (Workspace Manager, Session
Manager, Mission Control, the future desktop UI).

## What it ships with

Sprint-5 seeds the registry with the eight canonical Hermes
applications named in the directive:

| id | name | category |
| --- | --- | --- |
| `mission_control` | Mission Control | `mission_control` |
| `memory_galaxy` | Memory Galaxy | `memory` |
| `developer_studio` | Developer Studio | `developer` |
| `executive_dashboard` | Executive Dashboard | `dashboard` |
| `knowledge_explorer` | Knowledge Explorer | `knowledge` |
| `automation_center` | Automation Center | `automation` |
| `provider_manager` | Provider Manager | `provider` |
| `settings` | Settings | `settings` |

Every record carries a stable `id`, a `name`, a `description`, a
`category`, a `version`, an optional `route`, a list of
`capabilities_required`, a `status`, and an arbitrary
`entrypoint_metadata` dict. The workspace layer never invents
fields on top of this shape.

## Position in the stack

```
              WorkspaceManager ──reads──> ApplicationRegistry
              SessionManager  ──reads──> ApplicationRegistry
              MissionControl  ──reads──> ApplicationRegistry
              future desktop UI ──reads──> ApplicationRegistry
```

The registry is **read by** everyone and **depends on** nothing
but the optional EventBus. `WorkspaceManager` validates
`set_current_application` calls against the registry; the others
display and route.

## Public surface

```python
from hermes.modules.application_registry import build_application_registry

registry = build_application_registry()                # seeds 8 defaults
registry = build_application_registry(auto_register_defaults=False)  # empty

# Reads
app = registry.get_application("mission_control")      # Application | None
has = registry.has_application("missing")              # bool
apps = registry.list_applications()                    # sorted by (category, id)
apps = registry.list_applications(category="memory")   # filter

# Writes (sync -- no event publish)
app = Application(id="custom", name="Custom", category="custom", description="x")
registry.register_application(app)
removed = registry.remove_application("custom")
updated = registry.set_application_status("mission_control", "inactive")

# Writes (async -- publish application.* events)
await registry.register_application_async(app)
await registry.remove_application_async("custom")
await registry.set_application_status_async("mission_control", "inactive")
```

## Events

| Event | When |
| --- | --- |
| `application_registry.application.registered` | After `register_application_async` succeeds |
| `application_registry.application.removed` | After `remove_application_async` succeeds |
| `application_registry.application.activated` | After `set_application_status_async(_, "active")` and the previous status was not `active` |
| `application_registry.application.deactivated` | After `set_application_status_async(_, "inactive")` and the previous status was not `inactive` |

All four events carry `application_id`, `name`, and (for the
status events) `previous_status`. The full payload is documented
in `events.py`.

## Errors

| Exception | When |
| --- | --- |
| `DuplicateApplicationError` | `register_application(_async)` called with an id that is already in the catalog |
| `ApplicationNotFoundError` | `remove_application(_async)` or `set_application_status(_async)` called with an id that is not registered |
| `ApplicationRegistryError` | Base class for all registry-level errors |

## Backwards compatibility

- The eight default applications are guaranteed stable across
  Hermes v1; removing or renaming one of them is a breaking
  change to the future desktop UI and is forbidden by ADR.
- New applications can be added at runtime via
  `register_application_async`. The catalog is mutable; the
  *defaults* are not.
- The `Application` Pydantic model is the canonical shape. New
  fields are additive (always have a default). Renaming an
  existing field is forbidden.

## Out of scope (future Sprints)

- Per-user application installation.
- Per-workspace application visibility.
- Capability gating (an Application requiring `memory_read` is
  not currently refused for a user that lacks it; the registry
  just records what it requires).
- A binary launcher / sandbox manager. The registry is metadata
  only -- it never actually launches an application. A future
  Sprint will introduce a `Launcher` that consumes this catalog.
