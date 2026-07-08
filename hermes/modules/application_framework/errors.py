"""Application Framework-specific exception types.

- `UnknownApplicationError`: lookup / operation on an application
  that has not been registered with the Framework.
- `DuplicateApplicationInstanceError`: registration attempt for an
  id that is already registered.
- `ApplicationLifecycleError`: an invalid state transition (e.g.
  activating an app that is in `error` state).
- `ApplicationPermissionError`: an app attempted an operation
  requiring a permission it did not declare.
- `ApplicationFrameworkError`: base class.
"""
from __future__ import annotations


class ApplicationFrameworkError(Exception):
    """Base for application-framework-level errors."""


class UnknownApplicationError(ApplicationFrameworkError):
    def __init__(self, application_id: str) -> None:
        self.application_id = application_id
        super().__init__(f"application {application_id!r} is not registered with the framework")


class DuplicateApplicationInstanceError(ApplicationFrameworkError):
    def __init__(self, application_id: str) -> None:
        self.application_id = application_id
        super().__init__(f"application {application_id!r} is already registered")


class ApplicationLifecycleError(ApplicationFrameworkError):
    """Raised on an illegal state-machine transition."""

    def __init__(
        self,
        application_id: str,
        from_state: str,
        to_state: str,
        reason: str = "",
    ) -> None:
        self.application_id = application_id
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"application {application_id!r} cannot transition "
            f"{from_state!r} -> {to_state!r}"
            + (f": {reason}" if reason else "")
        )


class ApplicationPermissionError(ApplicationFrameworkError):
    """Raised when an operation requires a permission the app did not declare."""

    def __init__(self, application_id: str, missing_permission: str) -> None:
        self.application_id = application_id
        self.missing_permission = missing_permission
        super().__init__(
            f"application {application_id!r} lacks required permission "
            f"{missing_permission!r}"
        )


__all__ = [
    "ApplicationFrameworkError",
    "UnknownApplicationError",
    "DuplicateApplicationInstanceError",
    "ApplicationLifecycleError",
    "ApplicationPermissionError",
]