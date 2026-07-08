"""Provider Router event vocabulary.

Namespaced `provider_router.*`. Each event has a payload describing
one step of a routing decision so a future dashboard / replay tool can
reconstruct the full trail from the event log alone.
"""

ROUTING_STARTED = "provider_router.routing.started"
ROUTING_SUCCEEDED = "provider_router.routing.succeeded"
ROUTING_FAILED = "provider_router.routing.failed"
ROUTING_FAILOVER = "provider_router.routing.failover"
PROVIDER_ATTEMPT_STARTED = "provider_router.provider_attempt.started"
PROVIDER_ATTEMPT_SUCCEEDED = "provider_router.provider_attempt.succeeded"
PROVIDER_ATTEMPT_FAILED = "provider_router.provider_attempt.failed"

__all__ = [
    "ROUTING_STARTED",
    "ROUTING_SUCCEEDED",
    "ROUTING_FAILED",
    "ROUTING_FAILOVER",
    "PROVIDER_ATTEMPT_STARTED",
    "PROVIDER_ATTEMPT_SUCCEEDED",
    "PROVIDER_ATTEMPT_FAILED",
]
