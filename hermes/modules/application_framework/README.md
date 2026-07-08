# Application Framework

The canonical runtime model for every Hermes application. Sits
between Workspace Manager and Application Registry in the stack,
owns the lifecycle state machine, mediates with Workspace Manager
and Application Registry through Protocol surfaces, and publishes
`application_framework.*` events.

## Purpose

The Framework is the **operating system layer** between the
workspace shell and every Hermes application. It exists so that:

- **Every Hermes application implements the same ten-verb Protocol.**
  A Mission Control client, a Memory Galaxy client, a Developer
  Studio client, a third-party plugin, and a test double all
  satisfy `ApplicationProtocol` -- the workspace can talk to any
  of them uniformly.
- **Workspace integration is mediated, not direct.** An app
  declares a `WorkspaceIntegration` (route, window title pattern,
  focus events, workspaces). The framework forwards focus changes
  to the app via `on_workspace_focus`; it never lets the app
  mutate workspace state directly.
- **Future applications install without modifying Workspace
  Manager.** A plugin can `framework.register_application(self)`
  and immediately be a first-class participant in the workspace,
  with full lifecycle, event subscriptions, and routing support.

## Position in the stack

```
Workspace Manager ───depends on───> Application Framework ───depends on───> Application Registry
                                              │
                                              └── depends on ──> Event Bus
```

The Framework depends on:

- Workspace Manager via the **narrow `WorkspaceAccessor` Protocol**
  (re-declared in `contracts.py`).
- Application Registry via the **narrow `ApplicationSource` Protocol**
  (re-declared in `contracts.py`).
- The Hermes `EventBus` Protocol.

It does NOT import Workspace Manager's or Application Registry's
concrete classes. It does NOT launch processes, render UI, or call
business logic.

## Public surface

| Symbol | Purpose |
| --- | --- |
| `Application` | Runtime contract Pydantic model. |
| `LifecycleState` | Closed Literal: `unregistered` / `registered` / `starting` / `active` / `inactive` / `stopped` / `error`. |
| `LifecycleEvent` | One transition record. |
| `Permission` | Closed Literal of the framework's permission keys. |
| `EventSubscription` | Closed Literal of framework-recognized event prefixes. |
| `RoutingRequest` | Inbound routing request envelope. |
| `WorkspaceIntegration` | Workspace-integration metadata declared by an app. |
| `ApplicationFramework` | The framework. |
| `ApplicationProtocol` | The ten-verb contract every Hermes app implements. |
| `ApplicationFrameworkProtocol` | The framework's own surface for callers. |
| `WorkspaceAccessor`, `ApplicationSource` | Narrow re-declared Protocols. |
| `BaseApplication` | Convenience base class for `ApplicationProtocol` implementors (no-op defaults for simple apps). |
| `build_application_framework(...)` | Factory. |

## The lifecycle state machine

```
unregistered -> registered -> starting -> active <-> inactive
                                            |          |
                                            v          v
                                          error <-- stopped
                                            ^          |
                                            |          v
                                          error <-- registered (re-registration)
```

- `unregistered` is the implicit pre-state; an app enters
  `registered` the moment `framework.register_application(app)`
  succeeds.
- `starting` is entered on `startup_application()`; the framework
  awaits the app's `startup()`, transitions to `active` on success
  or `error` on exception.
- `active` <-> `inactive` via `activate_application()` /
  `deactivate_application()`.
- `stopped` is the terminal clean state; re-registration moves
  back to `registered`.

Every transition is recorded in `LifecycleEvent` records that
populate both the per-app history (`lifecycle_history(id)`) and a
bounded global ring (`recent_events()`).

## The ten verbs

| Verb | Method | Purpose |
| --- | --- | --- |
| 1. Startup | `async startup()` | One-shot init; called once on `startup_application`. |
| 2. Shutdown | `async shutdown()` | One-shot teardown; called once on `shutdown_application`. |
| 3. Activate | `async activate()` | Called when transitioning `inactive` -> `active`. |
| 4. Deactivate | `async deactivate()` | Called when transitioning `active` -> `inactive`. |
| 5. Get metadata | `get_metadata()` | Returns a Pydantic `Application` snapshot. |
| 6. Required capabilities | `get_required_capabilities()` | Capability keys the app needs to operate. |
| 7. Required permissions | `get_required_permissions()` | Permission keys the framework must hold on the app's behalf. |
| 8. Event subscriptions | `get_event_subscriptions()` | Event-type prefixes the app wants to receive. |
| 9. Workspace route | `get_workspace_route()` | Returns a `WorkspaceIntegration` (route, title, focus events). |
| 10. Workspace focus | `async on_workspace_focus(workspace_id, focused)` | Called when the workspace gains/loses focus on this app. |

Plus a routing verb:

| Verb | Method | Purpose |
| --- | --- | --- |
| 11. Routing | `async handle_routing(request)` | Receives a `RoutingRequest` dispatched by `framework.route(request)`. |

(Per the user's design choice: all ten lifecycle verbs are
**required** on `ApplicationProtocol`; `BaseApplication` provides
no-op defaults so simple apps can inherit rather than re-implement.
The Protocol itself remains strict.)

## Events

| Event | When |
| --- | --- |
| `application_framework.application.registered` | After `register_application_async` succeeds (the sync `register_application` does NOT publish) |
| `application_framework.application.unregistered` | After `unregister_application_async` succeeds (the sync `unregister_application` does NOT publish) |
| `application_framework.application.starting` | Before `app.startup()` is awaited |
| `application_framework.application.started` | After `app.startup()` returns successfully |
| `application_framework.application.activated` | After `activate_application` transitions to `active` |
| `application_framework.application.deactivated` | After `deactivate_application` transitions to `inactive` |
| `application_framework.application.stopped` | After `shutdown_application` transitions to `stopped` |
| `application_framework.application.error` | When `app.<verb>()` raises (payload carries `phase` + `error`) |

## Future plugin architecture

The Protocol-strict design + `register_application` API is the
foundation for a future plugin host. The plan is:

1. **Discovery.** A future `plugin_loader` module scans a directory
   (e.g. `~/.hermes/plugins/`) for Python packages that expose a
   `register(framework: ApplicationFramework) -> None` hook.
2. **Protocol gate.** The plugin's exported class MUST satisfy
   `ApplicationProtocol`; the framework's runtime `isinstance`
   check rejects non-conforming plugins.
3. **Lifecycle isolation.** Each plugin's lifecycle is owned by
   the framework; `startup_application`, `shutdown_application`,
   `activate_application`, `deactivate_application` work
   uniformly across built-in apps and plugins.
4. **Capability gating.** When a plugin declares
   `required_capabilities`, the framework cross-references them
   against the Capability Registry (via a future
   `CapabilitySource` Protocol re-declared here) and refuses to
   transition to `starting` if any capability is unmet.
5. **Permission gating.** Permissions are enforced at the
   framework boundary, not the app boundary. An app that
   requests a `Permission` not in its declared list gets an
   `ApplicationPermissionError` from the framework before the
   call is forwarded.
6. **Sandboxing (future).** A `policy` kwarg on
   `build_application_framework` will allow a future caller to
   supply a policy object that the framework consults on every
   verb (e.g. rate-limit activation events per minute).

The Plugin Loader is out of scope for Sprint-5b; the foundation
(Protocol, registry, lifecycle, events) is in place.

## Backwards compatibility

- `Application` field shapes are stable; new fields are additive.
- `LifecycleState` is a closed Literal; new states require an
  additive migration of any consumer.
- `Permission` and `EventSubscription` are closed Literals; new
  values are additive.
- All eight `application_framework.*` event constants are stable
  in name and payload schema.
- Apps implementing `ApplicationProtocol` continue to work
  unchanged; the framework adds no required arguments.

## Out of scope

- Process / sandbox launching.
- Capability Registry integration (a future `CapabilitySource`
  Protocol will close this gap).
- Permission enforcement on the framework side (today, declared
  permissions are recorded but not enforced; a future Sprint will
  gate them).
- Plugin Loader / plugin packaging / plugin signing.
- Real workspace integration (today the framework validates
  workspace ids and forwards focus events; the future desktop UI
  will be the actual focus source).