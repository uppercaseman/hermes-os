"""Application Registry Protocol contracts.

Defines the narrow surface other workspace modules depend on:

- `ApplicationSource` -- the read-only lookup surface. Workspace
  Manager uses it to validate an `application_id`; future modules
  use it for autocompletion / listings.

Both are `runtime_checkable` so tests can `isinstance` against
them without instantiating the real registry.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from hermes.modules.application_registry.models import Application, ApplicationCategory


@runtime_checkable
class ApplicationSource(Protocol):
    """Read-only lookup surface every consumer of the Registry uses.

    This is the same shape `WorkspaceManager` calls into for
    `set_current_application` validation -- a Protocol keeps the
    Workspace Manager from importing the Registry's concrete class.
    """

    def get_application(self, application_id: str) -> Application | None:
        ...

    def list_applications(
        self,
        *,
        category: ApplicationCategory | None = None,
    ) -> list[Application]:
        ...

    def has_application(self, application_id: str) -> bool:
        ...


__all__ = ["ApplicationSource"]
