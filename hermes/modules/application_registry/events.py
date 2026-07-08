"""Application Registry event vocabulary.

Namespaced `application_registry.*`. Four events fire whenever the
registry's contents change: register, remove, activate, deactivate.
A replay tool reading only the event log can reconstruct the
registry's mutation history.
"""

APPLICATION_REGISTERED = "application_registry.application.registered"
APPLICATION_REMOVED = "application_registry.application.removed"
APPLICATION_ACTIVATED = "application_registry.application.activated"
APPLICATION_DEACTIVATED = "application_registry.application.deactivated"

__all__ = [
    "APPLICATION_REGISTERED",
    "APPLICATION_REMOVED",
    "APPLICATION_ACTIVATED",
    "APPLICATION_DEACTIVATED",
]
