"""Protocol for anything the Supervisor can manage.

Hermes modules are event-driven -- they react to bus events, they do not
run a perpetual loop the way a web server does. That shapes this contract:
`start()` is expected to return once a module has finished initializing
(e.g. subscribed its event handlers), not to run forever. Because of that,
liveness *after* startup is judged by periodic `health_check()` polling,
not by watching a task run until it crashes -- see `Supervisor` in
service.py for how the two are used together.
"""
from __future__ import annotations

from typing import Protocol


class Supervisable(Protocol):
    async def start(self) -> None:
        """Initializes the module (e.g. subscribes its event handlers,
        opens resources) and returns once it is ready to operate. Raising
        here is treated as a startup crash and handled per the unit's
        restart strategy."""
        ...

    async def stop(self) -> None:
        """Releases resources / unsubscribes. Called on a deliberate
        shutdown (`Supervisor.stop` / `stop_all`); never itself triggers a
        restart, regardless of restart strategy."""
        ...

    async def health_check(self) -> bool:
        """Returns True if the module is healthy. Returning False is a
        graceful "I'm degraded" signal; raising is treated as a crash.
        Depending on the unit's restart strategy, either can trigger a
        restart."""
        ...
