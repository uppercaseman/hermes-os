"""Public entry point for the Application Registry.

Mirrors every other module's interface.py convention: outside callers
import from here, never from service.py directly. The Registry has no
required collaborators; the EventBus is optional and a `None` bus
silently skips every publish.
"""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.modules.application_registry.service import ApplicationRegistry

__all__ = ["ApplicationRegistry", "build_application_registry"]


def build_application_registry(
    *,
    event_bus: EventBus | None = None,
    auto_register_defaults: bool = True,
) -> ApplicationRegistry:
    """Constructs an ApplicationRegistry.

    `auto_register_defaults=True` (the default) seeds the registry
    with the eight canonical Hermes applications the Sprint-5
    directive names (Mission Control, Memory Galaxy, Developer
    Studio, Executive Dashboard, Knowledge Explorer, Automation
    Center, Provider Manager, Settings). Pass `False` to start with
    an empty registry -- handy for tests that exercise the
    registration API itself.
    """
    return ApplicationRegistry(
        event_bus=event_bus,
        auto_register_defaults=auto_register_defaults,
    )
