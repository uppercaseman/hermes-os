"""Task Queue-specific exception types."""
from __future__ import annotations

import uuid


class UnknownTaskError(Exception):
    def __init__(self, task_id: uuid.UUID) -> None:
        self.task_id = task_id
        super().__init__(f"no task with id {task_id}")


class InvalidTaskStateError(Exception):
    """Raised for an operation that doesn't make sense given a task's
    current status -- e.g. completing a task that was never claimed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
