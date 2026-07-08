"""Configuration Manager -- centralised, environment-safe configuration.

Four layers merge into one effective view, lowest to highest
precedence: registered schema **defaults**, the local **config file**,
**environment variables**, and runtime **overrides** (test harnesses,
a future dashboard). Every query method (`get`, `get_module_config`,
`get_provider_config`, `is_feature_enabled`, `get_dry_run`,
`export_safe`, `describe_all`) is synchronous and side-effect-free --
this is a plain in-memory dict, exactly like State Manager's queries,
and must never block a caller. Only methods that *change* something
(`set_override`, `clear_override`, `reload`) are async, because they
may publish a `configuration_manager.value.changed` event.

No background loop, no event subscription: unlike Logging System or
Capability Registry, Configuration Manager never listens to the bus,
only publishes to it. `start()`/`stop()` exist purely for interface
symmetry with every other module's lifecycle -- config is already
fully loaded and usable the instant `__init__` returns.
"""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import PydanticUndefined

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.configuration_manager import events as evt
from hermes.modules.configuration_manager.errors import ConfigValidationError, UnknownNamespaceError
from hermes.modules.configuration_manager.models import ConfigEntry, ConfigSnapshot
from hermes.modules.configuration_manager.sources import load_env_values, load_file_values
from hermes.modules.logging_system.redaction import default_redactor

SOURCE_MODULE = "configuration_manager"


def _redact_for_path(path: str, value: Any) -> Any:
    """Redacts `value` if `path`'s own name looks sensitive (e.g.
    "providers.openai.api_key" contains "key"). Reuses Logging System's
    `default_redactor` rather than re-implementing the same key-name
    heuristic a second time -- the one deliberate cross-module import
    this service makes, one-directional (Logging System has no
    knowledge of or dependency on this module)."""
    return default_redactor({path: value})[path]


class ConfigurationManager:
    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        config_file: str | None = None,
        env_prefix: str = "HERMES",
    ) -> None:
        self._event_bus = event_bus
        self._config_file = config_file
        self._env_prefix = env_prefix

        self._defaults: dict[str, Any] = {}
        self._file_values: dict[str, Any] = load_file_values(config_file) if config_file else {}
        self._env_values: dict[str, Any] = load_env_values(prefix=env_prefix)
        self._overrides: dict[str, Any] = {}
        self._schemas: dict[str, type[BaseModel]] = {}

        self._merged: dict[str, Any] = {}
        self._recompute_merged()

    async def start(self) -> None:
        """Publishes `configuration_manager.config.loaded` with a
        redacted snapshot, if an event bus was given. Never required
        before any other method works."""
        await self._publish(evt.CONFIG_LOADED, {"snapshot": self.export_safe()})

    async def stop(self) -> None:
        """No-op -- present for lifecycle symmetry only. Configuration
        Manager holds no background task or subscription to release."""
        return

    # ------------------------------------------------------------------ #
    # Schema registration + validation (#3 module-specific, #8 validation, #9 defaults)
    # ------------------------------------------------------------------ #
    def register_schema(
        self, namespace: str, schema: type[BaseModel], *, defaults: dict[str, Any] | None = None
    ) -> None:
        """Declares the shape a namespace's configuration must satisfy,
        with optional defaults merged in at the lowest precedence.
        Validates immediately against the *current* merged view (fail
        fast) -- raises `ConfigValidationError` if defaults plus
        whatever file/env/override values already exist don't satisfy
        `schema`. The namespace mechanism is generic: `"tool_manager"`
        for a module, `"providers.openai"` for a provider -- by
        convention only, `providers.<name>` is reserved for
        provider-specific config (#4), everything else is module-specific
        (#3), but both are the exact same code path.
        """
        for key, value in (defaults or {}).items():
            self._defaults[f"{namespace}.{key}"] = value
        # A schema's own field defaults (e.g. `dry_run: bool = True`)
        # also seed `_defaults`, at lower precedence than an explicit
        # `defaults=` entry for the same path. Without this,
        # `get_module_config()` would show a value for a field (via
        # pydantic's own default) that `get()`/`describe_all()`/
        # `export_safe()` never knew existed, since those only ever see
        # what's actually in `_merged` -- a real gap caught by this
        # module's own test suite, not a hypothetical one.
        for field_name, field_info in schema.model_fields.items():
            path = f"{namespace}.{field_name}"
            if path in self._defaults:
                continue
            resolved_default = field_info.get_default(call_default_factory=True)
            if resolved_default is not PydanticUndefined:
                self._defaults[path] = resolved_default
        self._recompute_merged()
        self._schemas[namespace] = schema
        self._validate_namespace(namespace)

    def _validate_namespace(self, namespace: str) -> BaseModel:
        schema = self._schemas[namespace]
        prefix = f"{namespace}."
        data = {path[len(prefix):]: value for path, value in self._merged.items() if path.startswith(prefix)}
        try:
            return schema(**data)
        except PydanticValidationError as exc:
            raise ConfigValidationError(namespace, exc.errors()) from exc

    def get_module_config(self, namespace: str) -> BaseModel:
        """Returns the validated schema instance for `namespace`.
        Raises `UnknownNamespaceError` if nothing was ever registered
        for it -- this is validated lookup, not raw lookup; use `get()`
        for a namespace nothing declared a schema for."""
        if namespace not in self._schemas:
            raise UnknownNamespaceError(namespace)
        return self._validate_namespace(namespace)

    def get_provider_config(self, provider_name: str) -> dict[str, Any]:
        """Raw (unvalidated) config for `providers.<provider_name>`, as
        a plain dict -- deliberately shaped so it can be spread
        directly into a provider adapter's constructor, e.g.
        `OpenAIAdapter(name="openai", **config.get_provider_config("openai"))`,
        without Configuration Manager needing to know that adapter's
        constructor signature or import anything from Tool Manager."""
        prefix = f"providers.{provider_name}."
        return {path[len(prefix):]: value for path, value in self._merged.items() if path.startswith(prefix)}

    # ------------------------------------------------------------------ #
    # Runtime lookup (#10)
    # ------------------------------------------------------------------ #
    def get(self, path: str, default: Any = None) -> Any:
        """Raw, unvalidated lookup by dotted path. Never raises -- an
        absent path returns `default`, exactly like `dict.get`."""
        return self._merged.get(path, default)

    def is_feature_enabled(self, flag_name: str, *, default: bool = False) -> bool:
        """Feature flags (#5) live under the reserved `feature_flags.*`
        namespace -- same generic mechanism as everything else, just a
        conventional prefix plus a bool coercion for convenience."""
        return bool(self._merged.get(f"feature_flags.{flag_name}", default))

    def get_dry_run(self, namespace: str, *, default: bool = True) -> bool:
        """Dry-run mode defaults (#6): checks `<namespace>.dry_run`
        first, then falls back to the process-wide `global.dry_run_default`,
        then to `default`. The fallback chain itself is the safety
        property -- unless a namespace, or the whole process, has
        explicitly opted out, this returns `True`, matching the same
        "safe by default" posture the OpenAI adapter already has
        built into its own constructor default."""
        specific = f"{namespace}.dry_run"
        if specific in self._merged:
            return bool(self._merged[specific])
        if "global.dry_run_default" in self._merged:
            return bool(self._merged["global.dry_run_default"])
        return default

    # ------------------------------------------------------------------ #
    # Test / dashboard overrides (#13) -- highest precedence, published as
    # change events (#14) so anything watching (Logging System, a future
    # dashboard) sees them happen.
    # ------------------------------------------------------------------ #
    async def set_override(self, path: str, value: Any) -> None:
        old_value = self._merged.get(path)
        self._overrides[path] = value
        self._recompute_merged()
        await self._publish_change(path, old_value, self._merged.get(path), source="override")

    async def clear_override(self, path: str) -> None:
        if path not in self._overrides:
            return
        self._overrides.pop(path)
        old_value = self._merged.get(path)  # stale until recompute below; captured for the event
        self._recompute_merged()
        await self._publish_change(path, old_value, self._merged.get(path), source="override")

    def clear_all_overrides(self) -> None:
        """Bulk reset for test teardown. Deliberately sync and silent
        (no per-key change events) -- this is a test-harness convenience
        clearing potentially many paths at once, not a runtime change
        any other module needs to individually react to."""
        self._overrides.clear()
        self._recompute_merged()

    # ------------------------------------------------------------------ #
    # Reload -- re-reads env + file, diffs against the previous snapshot
    # of each, and publishes one change event per path that actually
    # changed. Overrides are untouched: they are the highest-precedence
    # layer something set *deliberately*, and silently discarding them on
    # reload would defeat the point of having them.
    # ------------------------------------------------------------------ #
    async def reload(self) -> None:
        await self._reload_layer("env", self._env_values, load_env_values(prefix=self._env_prefix))
        if self._config_file:
            await self._reload_layer("file", self._file_values, load_file_values(self._config_file))
        await self._publish(evt.CONFIG_RELOADED, {"path_count": len(self._merged)})

    async def _reload_layer(self, source: str, layer: dict[str, Any], new_values: dict[str, Any]) -> None:
        for path in set(layer) | set(new_values):
            if layer.get(path) == new_values.get(path):
                continue
            old_effective = self._merged.get(path)
            if path in new_values:
                layer[path] = new_values[path]
            else:
                layer.pop(path, None)
            self._recompute_merged()
            await self._publish_change(path, old_effective, self._merged.get(path), source=source)

    # ------------------------------------------------------------------ #
    # Safe export (#7 secret redaction, #11 dashboard, #12 safe summaries)
    # ------------------------------------------------------------------ #
    def export_safe(self) -> dict[str, Any]:
        """A flat, redacted, JSON-serializable snapshot of every
        currently effective value -- safe to log, print, or hand to a
        future dashboard. Redaction runs against each dotted path
        itself, so "providers.openai.api_key_env_var" is redacted for
        containing the substring "key" even though its value is only
        the *name* of an env var, not a secret -- an intentionally
        conservative false positive, consistent with Logging System's
        own "over-redact rather than under-redact" posture."""
        return default_redactor(dict(self._merged))

    def describe_all(self) -> ConfigSnapshot:
        """Dashboard-ready structured view: every path, its redacted
        value, which layer it's currently winning from, and whether it
        falls under a namespace with a registered (and therefore
        validated) schema."""
        entries = [
            ConfigEntry(
                path=path,
                value=_redact_for_path(path, self._merged[path]),
                source=self._source_of(path),
                namespace_validated=self._is_validated(path),
            )
            for path in sorted(self._merged)
        ]
        feature_flags = {
            path[len("feature_flags."):]: bool(value)
            for path, value in self._merged.items()
            if path.startswith("feature_flags.")
        }
        return ConfigSnapshot(
            entries=entries, feature_flags=feature_flags, registered_namespaces=sorted(self._schemas)
        )

    def _source_of(self, path: str) -> str:
        if path in self._overrides:
            return "override"
        if path in self._env_values:
            return "env"
        if path in self._file_values:
            return "file"
        return "default"

    def _is_validated(self, path: str) -> bool:
        return any(path == namespace or path.startswith(f"{namespace}.") for namespace in self._schemas)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _recompute_merged(self) -> None:
        merged: dict[str, Any] = {}
        merged.update(self._defaults)
        merged.update(self._file_values)
        merged.update(self._env_values)
        merged.update(self._overrides)
        self._merged = merged

    async def _publish_change(self, path: str, old_value: Any, new_value: Any, *, source: str) -> None:
        await self._publish(
            evt.CONFIG_VALUE_CHANGED,
            {
                "path": path,
                "source": source,
                "old_value": _redact_for_path(path, old_value),
                "new_value": _redact_for_path(path, new_value),
            },
        )

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_bus is None:
            return
        await self._event_bus.publish(
            Event(event_type=event_type, source_module=SOURCE_MODULE, correlation_id=uuid.uuid4(), payload=payload)
        )
