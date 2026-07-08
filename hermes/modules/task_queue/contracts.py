"""Narrow Protocols for the Task Queue's pluggable pieces.

Same "depend on the shape you use, not a concrete class" pattern used
throughout this codebase.
"""
from __future__ import annotations

import uuid
from typing import Protocol

from hermes.modules.task_queue.models import QueuedTask, TaskExecutionResult


class TaskStorageBackend(Protocol):
    """Durable storage for tasks. `InMemoryTaskBackend` (backends.py) is
    the only implementation so far -- this Protocol is the extension
    point for a future SQLite/Postgres-backed one, per "start with an
    in-memory backend plus a persistence interface. Do not add database
    dependencies yet."
    """

    async def save(self, task: QueuedTask) -> None: ...
    async def get(self, task_id: uuid.UUID) -> QueuedTask | None: ...
    async def list_all(self) -> list[QueuedTask]: ...


class TaskExecutor(Protocol):
    """What a `Worker` calls to actually perform one task's work. Task
    Queue itself has no idea what a task DOES -- kept as a Protocol so
    Task Queue never depends on Workflow Engine or any other specific
    module; `workflow_executor.py`'s `WorkflowEngineTaskExecutor` is one
    concrete implementation, not the only possible one."""

    async def execute(self, task: QueuedTask) -> TaskExecutionResult: ...


class HeartbeatReporter(Protocol):
    """What a `Worker` needs from State Manager: just `report_heartbeat`,
    used to report busy/idle so a worker's liveness is visible the same
    way every other module's is."""

    async def report_heartbeat(self, module_name: str, state: str, *, detail: str | None = None) -> None: ...
