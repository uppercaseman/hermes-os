"""Public entry point for the Task Queue.

Everything outside this package imports from here, never from
service.py/worker.py directly -- mirrors every other module's
interface.py convention.
"""
from __future__ import annotations

from hermes.core.event_bus.interface import EventBus
from hermes.modules.task_queue.commander_dispatcher import TaskQueueDispatcher
from hermes.modules.task_queue.contracts import HeartbeatReporter, TaskExecutor, TaskStorageBackend
from hermes.modules.task_queue.errors import InvalidTaskStateError, UnknownTaskError
from hermes.modules.task_queue.models import QueuedTask, TaskExecutionResult, TaskStatus
from hermes.modules.task_queue.service import TaskQueue
from hermes.modules.task_queue.worker import Worker
from hermes.modules.task_queue.workflow_executor import WorkflowEngineTaskExecutor

__all__ = [
    "TaskQueue",
    "Worker",
    "TaskQueueDispatcher",
    "WorkflowEngineTaskExecutor",
    "QueuedTask",
    "TaskStatus",
    "TaskExecutionResult",
    "TaskStorageBackend",
    "TaskExecutor",
    "HeartbeatReporter",
    "UnknownTaskError",
    "InvalidTaskStateError",
    "build_task_queue",
    "build_worker",
]


def build_task_queue(
    *,
    backend: TaskStorageBackend | None = None,
    event_bus: EventBus | None = None,
    visibility_timeout_seconds: float = 60.0,
    max_claim_attempts: int = 3,
) -> TaskQueue:
    return TaskQueue(
        backend=backend,
        event_bus=event_bus,
        visibility_timeout_seconds=visibility_timeout_seconds,
        max_claim_attempts=max_claim_attempts,
    )


def build_worker(
    *,
    worker_id: str,
    queue: TaskQueue,
    executor: TaskExecutor,
    poll_interval_seconds: float = 0.5,
    state_manager: HeartbeatReporter | None = None,
) -> Worker:
    return Worker(
        worker_id=worker_id, queue=queue, executor=executor,
        poll_interval_seconds=poll_interval_seconds, state_manager=state_manager,
    )
