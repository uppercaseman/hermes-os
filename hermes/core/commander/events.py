"""Event-type constants Commander publishes and consumes.

Naming follows the OS-wide convention: `domain.entity.action`, past tense.
Commander's own events are namespaced `commander.*`. The `task.*` events
are published by the future Task Queue module -- Commander only consumes
them here, it never defines their schema.
"""

REQUEST_RECEIVED = "commander.request.received"
INTENT_DETERMINED = "commander.intent.determined"
WORKFLOW_DETERMINED = "commander.workflow.determined"
AGENTS_DETERMINED = "commander.agents.determined"
TOOLS_DETERMINED = "commander.tools.determined"
MEMORY_DETERMINED = "commander.memory.determined"
APPROVAL_REQUIRED = "commander.approval.required"
APPROVAL_GRANTED = "commander.approval.granted"
APPROVAL_NOT_REQUIRED = "commander.approval.not_required"
APPROVAL_DENIED = "commander.approval.denied"
TASK_DISPATCHED = "commander.task.dispatched"
TASK_RETRY_SCHEDULED = "commander.task.retry_scheduled"
RUN_COMPLETED = "commander.run.completed"
RUN_FAILED = "commander.run.failed"

# Consumed only -- owned and published by the future Task Queue module.
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"
