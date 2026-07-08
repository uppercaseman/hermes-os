"""Application Registry -- catalog of every Hermes application.

Exposes metadata only. No runtime logic; no UI. Every Hermes
application that the future desktop UI will launch is represented
as an `Application` record here, identified by a stable string id,
classified into a category, and carrying the entry-point metadata
the workspace needs to render and launch it.

The Registry is the **single source of truth** for "which apps
exist in Hermes today". `WorkspaceManager` consults it whenever an
application id is referenced; `MissionControl` and `SessionManager`
read from it for display and validation purposes. No other module
mutates the registry's contents at runtime -- registration of an
application happens once at process startup (defaults) and is
otherwise an explicit, traceable API call.
"""
from hermes.modules.application_registry.interface import build_application_registry
from hermes.modules.application_registry.service import ApplicationRegistry

__all__ = ["ApplicationRegistry", "build_application_registry"]
