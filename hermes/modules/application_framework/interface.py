"""Factory for Application Framework."""
from __future__ import annotations

from hermes.modules.application_framework.contracts import (
    ApplicationSource,
    WorkspaceAccessor,
)
from hermes.modules.application_framework.service import ApplicationFramework


def build_application_framework(
    *,
    workspace_manager: WorkspaceAccessor | None = None,
    application_registry: ApplicationSource | None = None,
    event_bus=None,
    history_size: int = 256,
) -> ApplicationFramework:
    """Construct an `ApplicationFramework`.

    All dependencies are Protocol-shaped and optional:

    - `workspace_manager`: any `WorkspaceAccessor` (re-declared from
      `workspace_manager`). When provided, the framework can validate
      workspace ids and mediate focus changes.
    - `application_registry`: any `ApplicationSource` (re-declared
      from `application_registry`). When provided, the framework
      cross-references declared capabilities against the catalog.
    - `event_bus`: the standard Hermes `EventBus` Protocol. When
      `None`, the framework operates silently (still works; just
      no `application_framework.*` events are published).
    - `history_size`: bounded ring buffer size for lifecycle history.

    Per Hermes convention, factory kwargs are keyword-only.
    """
    return ApplicationFramework(
        workspace_manager=workspace_manager,
        application_registry=application_registry,
        event_bus=event_bus,
        history_size=history_size,
    )


__all__ = ["build_application_framework"]