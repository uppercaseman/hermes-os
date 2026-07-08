"""Application Framework -- canonical runtime model for every Hermes application.

Provides the `Application` Protocol that every Hermes app must
implement, the lifecycle state machine (unregistered -> registered ->
starting -> active/inactive -> stopped), and the mediating layer
between Workspace Manager and Application Registry. Owns startup,
shutdown, activation, deactivation, permissions, capabilities,
routing, workspace integration, and event subscriptions.

The Framework is **pure runtime contract**. It does NOT launch a
process, render a UI, or call any business logic. It tracks lifecycle
state, mediates interactions with Workspace Manager and Application
Registry via their Protocol surfaces, and publishes
`application_framework.*` events on the EventBus.

A future plugin architecture (third-party apps installed at runtime)
is supported via the `register_application` API: any object that
satisfies the `Application` Protocol can be registered without
modifying Workspace Manager.
"""
from hermes.modules.application_framework.interface import build_application_framework
from hermes.modules.application_framework.service import ApplicationFramework, BaseApplication

__all__ = [
    "ApplicationFramework",
    "BaseApplication",
    "build_application_framework",
]