"""Commander-specific exception types.

These never escape `handle_request` / `resume_after_approval` to a caller
-- Commander catches them internally and turns them into a
`StructuredResponse` with `status="failed"`. They exist so the internal
failure reason is precise and testable, rather than an arbitrary caught
exception with a possibly-empty message (as `asyncio.TimeoutError` has by
default).
"""
from __future__ import annotations


class PlanningTimeoutError(Exception):
    """Raised internally when a planning-phase collaborator (the intent
    classifier, or the workflow/agent/tool/memory resolver) does not
    respond within Commander's configured `planning_timeout_seconds`.

    Naming the stage is what makes this debuggable: a bare
    `asyncio.TimeoutError` would tell you *that* planning hung, not
    *where*.
    """

    def __init__(self, stage: str, timeout_seconds: float) -> None:
        self.stage = stage
        self.timeout_seconds = timeout_seconds
        super().__init__(f"{stage} timed out after {timeout_seconds}s")
