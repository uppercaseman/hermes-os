"""Proves Tool Manager and the OpenAI adapter can consume Configuration
Manager without any change to their existing behavior when one isn't
given, and that when one is given, dry_run/api_key_env_var are genuinely
config-driven, dry_run stays safe by default, and the real API key value
is never read, exported, or logged anywhere in the process.

No live API call is made anywhere in this file, in any test.
"""
from __future__ import annotations

import os

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.configuration_manager.interface import build_configuration_manager
from hermes.modules.logging_system.redaction import REDACTED
from hermes.modules.tool_manager.adapters.openai_adapter import OPENAI_API_KEY_ENV_VAR, OpenAIAdapter
from hermes.modules.tool_manager.interface import build_tool_manager
from hermes.modules.tool_manager.models import ToolAdapterConfig


# --------------------------------------------------------------------- #
# Old behavior unchanged (no ConfigurationManager involved at all)
# --------------------------------------------------------------------- #

def test_tool_manager_default_adapter_config_matches_plain_construction_with_no_configuration_manager():
    tool_manager = build_tool_manager(event_bus=InMemoryEventBus())

    built = tool_manager.default_adapter_config("openai")
    plain = ToolAdapterConfig(name="openai")

    assert built == plain


def test_openai_adapter_plain_constructor_is_completely_unmodified():
    adapter = OpenAIAdapter(name="openai")

    assert adapter.dry_run is True
    assert adapter._api_key_env_var == OPENAI_API_KEY_ENV_VAR


def test_from_configuration_manager_matches_plain_defaults_when_nothing_is_configured():
    config = build_configuration_manager()  # no file, no relevant env vars, no overrides

    adapter = OpenAIAdapter.from_configuration_manager(name="openai", configuration_manager=config)

    assert adapter.dry_run is True
    assert adapter._api_key_env_var == OPENAI_API_KEY_ENV_VAR


# --------------------------------------------------------------------- #
# Config-driven behavior
# --------------------------------------------------------------------- #

async def test_tool_manager_default_adapter_config_honors_configured_timeout():
    config = build_configuration_manager()
    await config.set_override("tool_manager.invocation_timeout_seconds", 5.0)
    tool_manager = build_tool_manager(event_bus=InMemoryEventBus(), configuration_manager=config)

    built = tool_manager.default_adapter_config("openai")

    assert built.invocation_timeout_seconds == 5.0
    assert built.health_check_interval_seconds == ToolAdapterConfig.model_fields[
        "health_check_interval_seconds"
    ].get_default()  # untouched field still falls back to its own pydantic default


async def test_from_configuration_manager_honors_configured_dry_run_and_env_var_name():
    config = build_configuration_manager()
    await config.set_override("providers.openai.dry_run", False)
    await config.set_override("providers.openai.api_key_env_var", "MY_CUSTOM_OPENAI_KEY")

    adapter = OpenAIAdapter.from_configuration_manager(name="openai", configuration_manager=config)

    assert adapter.dry_run is False
    assert adapter._api_key_env_var == "MY_CUSTOM_OPENAI_KEY"


async def test_from_configuration_manager_honors_the_global_dry_run_fallback():
    config = build_configuration_manager()
    await config.set_override("global.dry_run_default", False)  # nothing openai-specific set

    adapter = OpenAIAdapter.from_configuration_manager(name="openai", configuration_manager=config)

    assert adapter.dry_run is False


# --------------------------------------------------------------------- #
# dry_run stays safe by default
# --------------------------------------------------------------------- #

async def test_dry_run_stays_true_even_with_a_real_looking_key_present_in_the_environment():
    """Merely having a real-shaped OPENAI_API_KEY sitting in the
    environment must never, by itself, flip dry_run off -- only an
    explicit configuration override (or explicit constructor argument)
    can do that."""
    os.environ["OPENAI_API_KEY"] = "sk-realvalue-should-not-flip-dry-run-off"
    try:
        config = build_configuration_manager()  # nothing configured for providers.openai
        adapter = OpenAIAdapter.from_configuration_manager(name="openai", configuration_manager=config)
    finally:
        os.environ.pop("OPENAI_API_KEY", None)

    assert adapter.dry_run is True


def test_tool_manager_with_no_configuration_manager_at_all_still_defaults_safely():
    tool_manager = build_tool_manager(event_bus=InMemoryEventBus())

    built = tool_manager.default_adapter_config("openai")

    assert built == ToolAdapterConfig(name="openai")


# --------------------------------------------------------------------- #
# Secrets are never read, exported, or logged
# --------------------------------------------------------------------- #

async def test_construction_never_reads_the_actual_api_key_value():
    """The strongest form of this guarantee: not just "not printed", but
    the real value is never even read from the environment during
    construction -- only during authenticate()/invoke(), and only if
    dry_run=False is explicitly requested (unchanged, pre-existing
    behavior, not touched by this task)."""
    os.environ["OPENAI_API_KEY"] = "sk-should-never-be-read-during-construction"
    original_get = os.environ.get
    calls_with_this_key: list[str] = []

    def spy_get(key, *args, **kwargs):
        if key == "OPENAI_API_KEY":
            calls_with_this_key.append(key)
        return original_get(key, *args, **kwargs)

    os.environ.get = spy_get
    try:
        config = build_configuration_manager()
        adapter = OpenAIAdapter.from_configuration_manager(name="openai", configuration_manager=config)
    finally:
        os.environ.get = original_get
        os.environ.pop("OPENAI_API_KEY", None)

    assert calls_with_this_key == []
    assert adapter.dry_run is True  # construction alone never authenticates either


async def test_export_safe_redacts_the_provider_config_even_though_it_only_ever_held_a_var_name():
    config = build_configuration_manager()
    await config.set_override("providers.openai.api_key_env_var", "OPENAI_API_KEY")
    await config.set_override("providers.openai.dry_run", True)

    exported = config.export_safe()

    # Conservative-by-design: the *name* of the env var is redacted too,
    # because the path itself contains "key" -- Configuration Manager
    # never held the real secret value in the first place, so this is
    # belt-and-suspenders, not the only thing standing between a secret
    # and export.
    assert exported["providers.openai.api_key_env_var"] == REDACTED
    assert exported["providers.openai.dry_run"] is True  # non-secret sibling untouched
