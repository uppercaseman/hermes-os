"""Event-type constants the Configuration Manager publishes.

Namespaced `configuration_manager.*`, following the OS-wide
`domain.entity.action` convention. This module never subscribes to
anything -- it is a publisher only, so this file is the entire vocabulary
other modules (notably Logging System, via its `"*"` wildcard
subscription) will ever see from it.
"""

CONFIG_LOADED = "configuration_manager.config.loaded"
CONFIG_RELOADED = "configuration_manager.config.reloaded"
CONFIG_VALUE_CHANGED = "configuration_manager.value.changed"
