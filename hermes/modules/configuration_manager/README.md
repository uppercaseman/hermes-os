# Hermes Configuration Manager

Centralised, environment-safe configuration for every other module.
Four layers merge into one effective view; every read is a plain
in-memory lookup; every write is an audited, redacted event.

## Architecture

```
hermes/modules/configuration_manager/
  __init__.py
  models.py       ConfigEntry, ConfigSnapshot -- dashboard-ready data
  errors.py       UnknownNamespaceError, ConfigValidationError
  events.py       CONFIG_LOADED, CONFIG_RELOADED, CONFIG_VALUE_CHANGED
  sources.py       load_env_values(), load_file_values(), flatten() -- pure functions, no state
  service.py       ConfigurationManager -- the merge + lookup + mutation engine
  interface.py       build_configuration_manager() + public re-exports
  tests/
    conftest.py
    test_sources.py
    test_schema.py
    test_service.py
    test_integration.py
```

### Four layers, one merged view

```
defaults  <  config file  <  environment variables  <  runtime overrides
(lowest)                                              (highest)
```

- **Defaults** come from `register_schema(namespace, Schema, defaults=...)` --
  both the explicit `defaults=` dict and, since a schema class already
  declares its own field defaults, the schema's own defaults too (a
  gap this module's own test suite caught: without seeding
  `_defaults` from the schema's fields, `get_module_config()` would
  report a value for a field that `get()`/`describe_all()`/
  `export_safe()` had no record of at all).
- **Config file** (`.json` or `.toml`, read via stdlib `json` /
  `tomllib` -- no new dependency) is loaded once at construction and
  again on `reload()`.
- **Environment variables** matching `<prefix>_<SEGMENT>__<SEGMENT>...`
  (default prefix `HERMES`) are loaded the same way. Double
  underscore separates path segments; a single underscore inside a
  segment (`TOOL_MANAGER`) is preserved. Example:
  `HERMES_PROVIDERS__OPENAI__DRY_RUN=true` -> path
  `providers.openai.dry_run`, value `True` (coerced from the string).
- **Runtime overrides** (`set_override()`/`clear_override()`) are for
  tests and a future dashboard -- always win, and are the only layer
  `reload()` never touches (reloading would otherwise silently
  discard a deliberate override, defeating its purpose).

Every layer is kept as its own dict; `_merged` is recomputed on any
change, so `describe_all()` can report exactly which layer is
currently winning for any given path.

### Module-specific vs. provider-specific: one mechanism, two conventions

There is no structural difference between "module config" and
"provider config" -- both are just dotted paths. `providers.<name>.*`
is a *reserved convention*, not a separate code path: `get_provider_config("openai")`
is a thin convenience that strips the `providers.openai.` prefix and
hands back a plain dict, shaped so it can be spread directly into a
provider adapter's constructor
(`OpenAIAdapter(name="openai", **config.get_provider_config("openai"))`)
without Configuration Manager ever importing or knowing about
Tool Manager or any adapter. Everything else is addressed by module
name (`tool_manager.*`, `capability_registry.*`, ...), same mechanism.

Configuration Manager itself defines **no** provider-specific or
module-specific schema in its own production code -- `OpenAIProviderConfig`
in `test_integration.py` is caller-supplied, living in the test that
needs it. This keeps the module a generic mechanism, not a repository
of other modules' knowledge, exactly per this task's "build the
infrastructure, not the integrations" instruction.

### Dry-run defaults: a dedicated fallback chain, not just a path lookup

`get_dry_run(namespace, default=True)` checks, in order:
`<namespace>.dry_run` -> `global.dry_run_default` -> the `default`
argument (itself `True`). The chain is the safety property: unless a
specific namespace or the whole process has explicitly opted out,
every caller gets the same "safe by default" answer the OpenAI
adapter's own constructor already defaults to (`dry_run: bool = True`)
-- Configuration Manager's default agrees with that adapter's default
without either one depending on the other.

### Validation is opt-in, per namespace

`register_schema()` is how a module (or, in this codebase, an
integration test standing in for one) declares the shape its config
must satisfy. Validation runs immediately at registration (fail
fast) and again on every `get_module_config()` call, so a `reload()`
that picks up a bad value surfaces a `ConfigValidationError` the next
time that namespace is read, not silently. A namespace nobody
registered a schema for is still fully readable via `get()` /
`get_provider_config()` -- validation is additive, never required.

### Redaction: reused, not reimplemented

`export_safe()` and every `configuration_manager.value.changed` event
payload run through Logging System's `default_redactor` -- the one
deliberate cross-module import this service makes
(`hermes.modules.logging_system.redaction`), one-directional (Logging
System has no dependency on or knowledge of Configuration Manager).
This is the same "reuse the existing building block" idiom as
`RetryPolicy` being shared by six other modules. Redaction runs
against the *dotted path itself* -- `"providers.openai.api_key_env_var"`
is redacted for containing the substring `"key"`, even though its
value is only the *name* of an env var, not a secret. That's a
deliberate, conservative false positive: over-redaction is the safe
failure mode here, exactly as documented in Logging System's own
README.

### "Raise on misuse, default on absence"

`get_module_config()` raises `UnknownNamespaceError` for a namespace
nothing registered a schema for -- that's a caller error. `get()`,
`get_provider_config()`, and `is_feature_enabled()` never raise; an
absent path returns the caller's supplied default (or `None`/`False`).
Same rule every other module in this codebase follows.

### Sync queries, async mutations

`get`, `get_module_config`, `get_provider_config`, `is_feature_enabled`,
`get_dry_run`, `export_safe`, and `describe_all` are all **synchronous**
-- this is a plain in-memory dict, and per the same rule State Manager,
Workflow Engine, and Mission System already follow, a query must never
be able to block a caller. Only `set_override`, `clear_override`,
`reload`, `start`, and `stop` are **async**, because (and only
because) they may publish an event.

### No subscription, no background loop

Unlike Logging System or Capability Registry, Configuration Manager
never listens to the Event Bus -- it only publishes to it.
`start()`/`stop()` exist purely for lifecycle symmetry with every
other module's `build_x()` convention; `start()`'s only job is
publishing `configuration_manager.config.loaded` with a redacted
snapshot, and `stop()` is a documented no-op. Configuration is fully
loaded and every method fully usable the instant `build_configuration_manager()`
returns, with or without ever calling `start()`.

## Requirement -> mechanism map

| Requirement | Mechanism |
|---|---|
| Load from environment variables | `sources.load_env_values()` -- `HERMES_<SEGMENT>__<SEGMENT>...` |
| Load from local config files | `sources.load_file_values()` -- `.json` (stdlib `json`) or `.toml` (stdlib `tomllib`) |
| Module-specific configuration | `register_schema("tool_manager", ...)` / `get_module_config("tool_manager")` |
| Provider-specific configuration | `register_schema("providers.openai", ...)` / `get_provider_config("openai")` |
| Feature flags | `is_feature_enabled(flag_name)` -- reserved `feature_flags.*` prefix |
| Dry-run mode defaults | `get_dry_run(namespace)` -- namespace -> global -> `True` fallback chain |
| Secret redaction | `export_safe()` / change-event payloads, via Logging System's `default_redactor` |
| Validation | `register_schema()` -- fail-fast at registration, re-checked on every `get_module_config()` |
| Defaults | `register_schema(..., defaults=...)` plus the schema's own field defaults |
| Runtime config lookup | `get(path, default)` -- synchronous, side-effect-free |
| Future UI/dashboard editing support | `describe_all() -> ConfigSnapshot` -- JSON-serializable, per-entry source + validation status |
| Exporting safe config summaries | `export_safe() -> dict` -- flat, redacted, JSON-safe |
| Test configuration overrides | `set_override()` / `clear_override()` / `clear_all_overrides()` |
| Configuration change events | `configuration_manager.value.changed`, published on every override, env, or file change |

## What's real

- Real env var scanning and coercion (bool/int/float/JSON), real `.json`/`.toml` file loading, both exercised against real files and real `os.environ` in `test_sources.py`.
- Real four-layer precedence merge, real fail-fast schema validation, real dry-run fallback chain.
- Real event publishing for load/reload/change, real redaction (reused from Logging System, not reimplemented).
- `test_integration.py` proves, against real (not mocked) instances:
  - **Event Bus + Logging System**: a real `LoggingSystem` captures Configuration Manager's own events and independently re-confirms the secret is redacted.
  - **Tool Manager + a provider package**: `get_provider_config("openai")`'s output constructs and drives a real, unmodified `OpenAIAdapter` through a real `ToolManager.invoke()` call -- flipping the config's `dry_run` override changes what the adapter actually does.
  - **Capability Registry**: a config-sourced value (`capability_registry.overrides.code_generation`) drives a real `set_override()` call that changes real selection ranking.
  - **State Manager**: Configuration Manager reports its own heartbeat, exactly like every other module.

## What's placeholder only

- No module has actually been wired to *read its own real defaults* from Configuration Manager yet -- every existing module (Tool Manager, Capability Registry, State Manager, the OpenAI adapter, etc.) still hardcodes its own constructor defaults in Python, unmodified by this task. `test_integration.py` proves the *shape* of that integration works end to end; nothing calls it automatically today.
- No `.env` file support (only `.json`/`.toml`) -- adding it would be a small, additive change to `sources.py` if ever needed.
- No config-file *watching* -- `reload()` must be called explicitly; there is no filesystem watcher triggering it automatically.
- No encryption-at-rest or secrets-manager integration -- "do not hardcode secrets" is enforced by convention (secrets live in env vars, referenced by *name* through config, e.g. `api_key_env_var`) and by redaction on read/export, not by any storage-layer protection.

## How it integrates with Tool Manager, providers, and Logging System

**Tool Manager / provider packages**: as of the follow-up task that
wired this in for real, `ToolManager` optionally accepts a
`ConfigurationManager` (`build_tool_manager(..., configuration_manager=config)`)
and `OpenAIAdapter.from_configuration_manager(name=..., configuration_manager=config)`
is a real, tested alternative constructor -- see
`hermes/modules/tool_manager/README.md`'s "Consuming Configuration
Manager" section and `tool_manager/tests/test_configuration_manager_wiring.py`
for the full detail. The dependency direction is still one-way
(Tool Manager imports Configuration Manager, never the reverse), and
both integrations are additive: omitting `configuration_manager`
reproduces every prior behavior of both classes exactly, proven by a
full regression run of Tool Manager's pre-existing test suite
alongside the new wiring tests.

**Logging System**: purely via the Event Bus, no direct dependency in
that direction -- Configuration Manager publishes `configuration_manager.*`
events like any other module, and Logging System's existing `"*"`
wildcard subscription captures them automatically, no changes to
Logging System required. Configuration Manager separately imports
Logging System's `default_redactor` (a one-directional, read-only
reuse) so a secret is redacted twice over before it could ever reach
storage: once by Configuration Manager before publishing, once again,
idempotently, by Logging System's own capture-time redaction.

## Known architectural gaps

1. ~~No module actually consumes Configuration Manager's output automatically yet.~~ **Closed** -- `ToolManager`/`OpenAIAdapter` now do, optionally and additively. See `hermes/modules/tool_manager/README.md`. Every *other* module (Capability Registry, State Manager, Memory Manager, Workflow Engine, Task Queue, Logging System, Mission System) still hardcodes its own defaults -- this closes the gap for exactly one module, as a proof of pattern, not for all of them.
2. **`reload()` must be triggered explicitly.** There is no filesystem watcher and no polling loop -- a changed config file has no effect until something calls `await config.reload()`.
3. **Provider-specific schemas live outside this module.** This is intentional (see "Module-specific vs. provider-specific" above) but means there is no single place in the codebase today declaring "here is every provider's expected config shape" -- each integration declares its own schema locally, same as `OpenAIProviderConfig` in `configuration_manager/tests/test_integration.py`.
4. **Redaction is the same best-effort mechanism Logging System already documents as best-effort** -- a secret under a non-obviously-named key and not matching a known provider key-prefix shape would not be caught.
5. **Tool Manager's wiring only covers two scalar `ToolAdapterConfig` fields** (`invocation_timeout_seconds`, `health_check_interval_seconds`) -- the nested `rate_limit`/`retry_policy`/`auth` models are not sourced from config yet.

## Safest next step

Repeat the same pattern for **one more already-built module** --
Capability Registry is the best next candidate: its `set_override()`/
`set_provider_enabled()` are already proven, config-drivable in
`configuration_manager/tests/test_integration.py`, just not wired as
an optional constructor path the way Tool Manager's now is. Wiring
`build_capability_registry(..., configuration_manager=config)` to
apply any `capability_registry.overrides.*`/`capability_registry.disabled.*`
values at construction would be the same small, additive, fully
backward-compatible shape as this task, one module at a time.
