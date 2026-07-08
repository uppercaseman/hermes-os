"""Application Registry-specific exception types.

The Registry surfaces three failure modes distinctly:

- `DuplicateApplicationError` -- attempt to register an id that is
  already in the catalog.
- `ApplicationNotFoundError` -- lookup or removal of an id that was
  never registered (or was removed).
- `ApplicationRegistryError` -- base class for all registry-level
  errors.
"""
from __future__ import annotations


class ApplicationRegistryError(Exception):
    """Base for other registry-level errors."""


class DuplicateApplicationError(ApplicationRegistryError):
    def __init__(self, application_id: str) -> None:
        self.application_id = application_id
        super().__init__(
            f"application {application_id!r} is already registered"
        )


class ApplicationNotFoundError(ApplicationRegistryError):
    def __init__(self, application_id: str) -> None:
        self.application_id = application_id
        super().__init__(
            f"application {application_id!r} is not registered"
        )


__all__ = [
    "ApplicationRegistryError",
    "DuplicateApplicationError",
    "ApplicationNotFoundError",
]
