"""Severity inference for captured events.

No module built so far actually sets `Event.level` to anything but the
default `"info"` -- even for `*.failed`/`*.dead_lettered` events (checked
across Commander, Supervisor, Tool Manager, Memory Manager, Capability
Registry, State Manager, Workflow Engine, Mission System, and Task
Queue's own publish call sites). Retroactively editing every one of
those to set a meaningful level would be a much larger, riskier change
than this task calls for. Instead, Logging System infers a more useful
severity from the event_type STRING itself, falling back to the event's
own `level` field only when it was deliberately set to something other
than the default -- so a module that DOES start setting real levels
later is honored, not overridden.
"""
from __future__ import annotations

_ERROR_KEYWORDS = ("failed", "dead_letter", "crashed", "denied", "unavailable", "exhausted")
_WARN_KEYWORDS = ("retry", "unhealthy", "degraded", "recovered", "skipped")


def classify_severity(event_type: str, level: str) -> str:
    if level in ("error", "warn"):
        return level
    lowered = event_type.lower()
    if any(keyword in lowered for keyword in _ERROR_KEYWORDS):
        return "error"
    if any(keyword in lowered for keyword in _WARN_KEYWORDS):
        return "warn"
    return level
