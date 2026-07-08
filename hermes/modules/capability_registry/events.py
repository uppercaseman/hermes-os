"""Event-type constants the Capability Registry publishes.

Namespaced `capability_registry.*`. All publishing is a no-op if the
registry was constructed without an event bus -- see service.py.
"""

SELECTION_MADE = "capability_registry.selection.made"
SELECTION_UNAVAILABLE = "capability_registry.selection.unavailable"
OVERRIDE_SET = "capability_registry.override.set"
OVERRIDE_CLEARED = "capability_registry.override.cleared"
PROVIDER_ENABLED = "capability_registry.provider.enabled"
PROVIDER_DISABLED = "capability_registry.provider.disabled"
HEALTH_UPDATED = "capability_registry.health.updated"
