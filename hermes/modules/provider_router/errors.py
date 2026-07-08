"""Provider Router-specific exception types.

`NoProviderAvailableError` is reserved for the unrecoverable case where
Capability Registry returned no candidate at all. Fail-over exhaustion
returns a structured `ProviderInvocationOutcome` with `success=False`
rather than raising -- matching the rest of Hermes' "failures are
data" convention.
"""
from __future__ import annotations


class NoProviderAvailableError(Exception):
    """Raised when the Capability Registry returned no candidate for a
    capability at all (i.e. nothing was ever registered). Distinct from
    "all registered providers failed at runtime," which returns a
    `ProviderInvocationOutcome` with `success=False`."""

    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__(
            f"no provider has been registered for capability {capability!r}"
        )


class ProviderRouterError(Exception):
    """Base for other router-level errors."""


class InvalidRoutingRequestError(ProviderRouterError):
    """Raised when a `RoutingRequest` is missing required fields."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"invalid routing request: {reason}")


__all__ = ["NoProviderAvailableError", "InvalidRoutingRequestError", "ProviderRouterError"]
