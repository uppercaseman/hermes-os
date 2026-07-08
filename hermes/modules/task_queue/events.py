"""Event-type constants the Task Queue publishes.

`TASK_COMPLETED`/`TASK_FAILED` are deliberately the exact strings
Commander's own `_dispatch_and_await` already subscribes to
(core/commander/events.py's `TASK_COMPLETED`/`TASK_FAILED`,
"task.completed"/"task.failed", unmodified). Task Queue publishing
these itself, once a task reaches a terminal state, is what lets
Commander's `TaskDispatcher` contract be satisfied by a real, durable
queue without Commander ever knowing one exists. Every other event here
is Task Queue's own, richer vocabulary.
"""

TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"

TASK_ENQUEUED = "task_queue.task.enqueued"
TASK_CLAIMED = "task_queue.task.claimed"
TASK_RETRY_SCHEDULED = "task_queue.task.retry_scheduled"
TASK_DEAD_LETTERED = "task_queue.task.dead_lettered"
TASK_RECOVERED = "task_queue.task.recovered"
