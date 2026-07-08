"""Public entry point for the Logging System.

Everything outside this package imports from here, never from
service.py directly -- mirrors every other module's interface.py
convention.
"""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.modules.logging_system.contracts import LogStorageBackend
from hermes.modules.logging_system.errors import UnknownLogEntryError
from hermes.modules.logging_system.models import LogEntry, Severity
from hermes.modules.logging_system.redaction import REDACTED, RedactionHook, default_redactor
from hermes.modules.logging_system.service import LoggingSystem

__all__ = [
    "LoggingSystem",
    "LogEntry",
    "Severity",
    "LogStorageBackend",
    "RedactionHook",
    "default_redactor",
    "REDACTED",
    "UnknownLogEntryError",
    "build_logging_system",
]


def build_logging_system(
    *,
    event_bus: EventBus | None = None,
    backend: LogStorageBackend | None = None,
    redaction_hook: RedactionHook | None = None,
) -> LoggingSystem:
    return LoggingSystem(event_bus=event_bus, backend=backend, redaction_hook=redaction_hook)
