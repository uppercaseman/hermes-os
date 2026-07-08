"""Intent Router-specific exception types."""
from __future__ import annotations


class UnknownIntentError(Exception):
    """Raised when no route matched a request and no `default_workflow_id`
    was configured. This is what makes the router genuinely
    discriminating: unmatched input fails clearly rather than silently
    falling through to some workflow."""

    def __init__(self, raw_input: str) -> None:
        self.raw_input = raw_input
        super().__init__(f"no workflow route matched request: {raw_input!r}")
