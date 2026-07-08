"""Application Framework event vocabulary.

Namespaced `application_framework.*`. Eight events cover the full
runtime lifecycle: register, unregister, starting, started,
activated, deactivated, stopped, error. All eight are distinct from
the four `application_registry.application.*` events (which are
catalog-mutation events); these describe *runtime* transitions.
"""

APPLICATION_REGISTERED = "application_framework.application.registered"
APPLICATION_UNREGISTERED = "application_framework.application.unregistered"
APPLICATION_STARTING = "application_framework.application.starting"
APPLICATION_STARTED = "application_framework.application.started"
APPLICATION_ACTIVATED = "application_framework.application.activated"
APPLICATION_DEACTIVATED = "application_framework.application.deactivated"
APPLICATION_STOPPED = "application_framework.application.stopped"
APPLICATION_ERROR = "application_framework.application.error"

__all__ = [
    "APPLICATION_REGISTERED",
    "APPLICATION_UNREGISTERED",
    "APPLICATION_STARTING",
    "APPLICATION_STARTED",
    "APPLICATION_ACTIVATED",
    "APPLICATION_DEACTIVATED",
    "APPLICATION_STOPPED",
    "APPLICATION_ERROR",
]