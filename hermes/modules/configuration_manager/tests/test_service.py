import json
import os
import tempfile
from pathlib import Path

from hermes.modules.configuration_manager.events import CONFIG_RELOADED, CONFIG_VALUE_CHANGED
from hermes.modules.configuration_manager.interface import build_configuration_manager
from hermes.modules.logging_system.redaction import REDACTED


def _capture(sink: list):
    """The Event Bus expects every subscriber to be an async callable --
    a plain sync lambda technically "works" by accident (its side
    effect runs before `await`ing its `None` return value fails), but
    trips the bus's own fault-isolation warning. This is the correct
    shape."""

    async def _handler(event) -> None:
        sink.append(event)

    return _handler


# --------------------------------------------------------------------- #
# Runtime lookup (#10)
# --------------------------------------------------------------------- #

def test_get_returns_default_for_an_unknown_path(config_manager):
    assert config_manager.get("nothing.here", "fallback") == "fallback"


def test_get_returns_none_by_default_for_an_unknown_path(config_manager):
    assert config_manager.get("nothing.here") is None


# --------------------------------------------------------------------- #
# Feature flags (#5)
# --------------------------------------------------------------------- #

def test_feature_flag_defaults_to_false(config_manager):
    assert config_manager.is_feature_enabled("some_flag") is False


def test_feature_flag_defaults_can_be_overridden_by_caller(config_manager):
    assert config_manager.is_feature_enabled("some_flag", default=True) is True


async def test_feature_flag_can_be_set_via_override(config_manager):
    await config_manager.set_override("feature_flags.new_ui", True)

    assert config_manager.is_feature_enabled("new_ui") is True


# --------------------------------------------------------------------- #
# Dry-run mode defaults (#6)
# --------------------------------------------------------------------- #

def test_dry_run_defaults_to_true_with_nothing_configured(config_manager):
    assert config_manager.get_dry_run("providers.openai") is True


async def test_dry_run_honors_a_namespace_specific_override(config_manager):
    await config_manager.set_override("providers.openai.dry_run", False)

    assert config_manager.get_dry_run("providers.openai") is False
    assert config_manager.get_dry_run("providers.claude") is True  # unaffected


async def test_dry_run_honors_the_global_fallback_when_namespace_unset(config_manager):
    await config_manager.set_override("global.dry_run_default", False)

    assert config_manager.get_dry_run("providers.anything") is False


# --------------------------------------------------------------------- #
# Provider-specific configuration (#4)
# --------------------------------------------------------------------- #

async def test_get_provider_config_returns_only_that_providers_keys(config_manager):
    await config_manager.set_override("providers.openai.dry_run", True)
    await config_manager.set_override("providers.openai.api_key_env_var", "OPENAI_API_KEY")
    await config_manager.set_override("providers.claude.dry_run", False)

    openai_config = config_manager.get_provider_config("openai")

    assert openai_config == {"dry_run": True, "api_key_env_var": "OPENAI_API_KEY"}


def test_get_provider_config_is_empty_dict_for_an_unconfigured_provider(config_manager):
    assert config_manager.get_provider_config("nonexistent") == {}


# --------------------------------------------------------------------- #
# Test / dashboard overrides (#13) + change events (#14)
# --------------------------------------------------------------------- #

async def test_set_override_takes_precedence_over_everything_else(config_manager):
    await config_manager.set_override("a.b", "override_value")

    assert config_manager.get("a.b") == "override_value"


async def test_clear_override_reverts_to_whatever_is_beneath_it(config_manager):
    await config_manager.set_override("a.b", "override_value")
    await config_manager.clear_override("a.b")

    assert config_manager.get("a.b") is None


async def test_clear_override_of_a_never_set_path_is_a_silent_no_op(config_manager):
    await config_manager.clear_override("never.set")  # must not raise


def test_clear_all_overrides_resets_every_override_at_once(config_manager):
    import asyncio

    asyncio.run(config_manager.set_override("a.b", 1))
    asyncio.run(config_manager.set_override("c.d", 2))

    config_manager.clear_all_overrides()

    assert config_manager.get("a.b") is None
    assert config_manager.get("c.d") is None


async def test_set_override_publishes_a_change_event(bus):
    config = build_configuration_manager(event_bus=bus)
    seen = []
    await bus.subscribe(CONFIG_VALUE_CHANGED, _capture(seen))

    await config.set_override("a.b", "new_value")

    assert len(seen) == 1
    assert seen[0].payload == {"path": "a.b", "source": "override", "old_value": None, "new_value": "new_value"}


async def test_set_override_change_event_redacts_a_secret_looking_path(bus):
    config = build_configuration_manager(event_bus=bus)
    seen = []
    await bus.subscribe(CONFIG_VALUE_CHANGED, _capture(seen))

    await config.set_override("providers.openai.api_key", "sk-should-never-appear-in-an-event")

    assert seen[0].payload["new_value"] == REDACTED


# --------------------------------------------------------------------- #
# Reload (#1 env, #2 file)
# --------------------------------------------------------------------- #

async def test_reload_picks_up_a_newly_set_env_var(bus):
    config = build_configuration_manager(event_bus=bus, env_prefix="HERMES_RELOAD_TEST")
    seen = []
    await bus.subscribe(CONFIG_VALUE_CHANGED, _capture(seen))

    os.environ["HERMES_RELOAD_TEST_A__B"] = "1"
    try:
        await config.reload()
    finally:
        os.environ.pop("HERMES_RELOAD_TEST_A__B", None)

    assert config.get("a.b") == 1
    assert any(e.payload["path"] == "a.b" and e.payload["source"] == "env" for e in seen)


async def test_reload_picks_up_file_changes(bus):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        path.write_text(json.dumps({"feature_flags": {"x": False}}))

        config = build_configuration_manager(event_bus=bus, config_file=str(path))
        assert config.get("feature_flags.x") is False

        path.write_text(json.dumps({"feature_flags": {"x": True}}))
        await config.reload()

        assert config.get("feature_flags.x") is True


async def test_reload_does_not_touch_overrides(bus):
    config = build_configuration_manager(event_bus=bus)
    await config.set_override("a.b", "pinned")

    await config.reload()

    assert config.get("a.b") == "pinned"


async def test_reload_publishes_a_reloaded_event(bus):
    config = build_configuration_manager(event_bus=bus)
    seen = []
    await bus.subscribe(CONFIG_RELOADED, _capture(seen))

    await config.reload()

    assert len(seen) == 1


# --------------------------------------------------------------------- #
# Safe export (#7 redaction, #11 dashboard, #12 safe summaries)
# --------------------------------------------------------------------- #

async def test_export_safe_redacts_secret_looking_paths(config_manager):
    await config_manager.set_override("providers.openai.api_key", "sk-realvalue123456789")

    exported = config_manager.export_safe()

    assert exported["providers.openai.api_key"] == REDACTED


async def test_export_safe_keeps_non_secret_values_intact(config_manager):
    await config_manager.set_override("providers.openai.dry_run", True)

    exported = config_manager.export_safe()

    assert exported["providers.openai.dry_run"] is True


async def test_describe_all_reports_source_per_entry(config_manager):
    await config_manager.set_override("a.b", 1)

    snapshot = config_manager.describe_all()

    entry = next(e for e in snapshot.entries if e.path == "a.b")
    assert entry.source == "override"


def test_describe_all_reports_which_namespaces_are_schema_validated(config_manager):
    from pydantic import BaseModel

    class _Schema(BaseModel):
        x: int = 1

    config_manager.register_schema("some_namespace", _Schema)

    snapshot = config_manager.describe_all()
    entry = next(e for e in snapshot.entries if e.path == "some_namespace.x")
    assert entry.namespace_validated is True


async def test_describe_all_reports_feature_flags_separately(config_manager):
    await config_manager.set_override("feature_flags.new_ui", True)

    snapshot = config_manager.describe_all()

    assert snapshot.feature_flags == {"new_ui": True}


# --------------------------------------------------------------------- #
# Lifecycle (start/stop no-op symmetry)
# --------------------------------------------------------------------- #

async def test_start_publishes_config_loaded(bus):
    from hermes.modules.configuration_manager.events import CONFIG_LOADED

    config = build_configuration_manager(event_bus=bus)
    seen = []
    await bus.subscribe(CONFIG_LOADED, _capture(seen))

    await config.start()

    assert len(seen) == 1
    assert "snapshot" in seen[0].payload


async def test_stop_is_a_harmless_no_op(config_manager):
    await config_manager.stop()  # must not raise, even though start() was never called
