"""Public entry point for the Configuration Manager.

Everything outside this module -- other modules, the CLI, tests --
imports from here, never from service.py directly. Mirrors every other
module's interface.py convention.
"""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.modules.configuration_manager.errors import ConfigValidationError, UnknownNamespaceError
from hermes.modules.configuration_manager.models import ConfigEntry, ConfigSnapshot, ConfigSource
from hermes.modules.configuration_manager.service import ConfigurationManager

__all__ = [
    "ConfigurationManager",
    "ConfigEntry",
    "ConfigSnapshot",
    "ConfigSource",
    "UnknownNamespaceError",
    "ConfigValidationError",
    "build_configuration_manager",
]


def build_configuration_manager(
    *,
    event_bus: EventBus | None = None,
    config_file: str | None = None,
    env_prefix: str = "HERMES",
) -> ConfigurationManager:
    """Constructs a Configuration Manager. Loads `config_file` (if any,
    `.json` or `.toml`) and every `<env_prefix>_<SEGMENT>__<SEGMENT>...`
    environment variable immediately and synchronously -- configuration
    is fully available the instant this returns. Call `await start()`
    on the result only if you want the `configuration_manager.config.loaded`
    event published; every other method works regardless."""
    return ConfigurationManager(event_bus=event_bus, config_file=config_file, env_prefix=env_prefix)
