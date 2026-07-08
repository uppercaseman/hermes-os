"""Event-type constants the Workflow Engine publishes.

Namespaced `workflow_engine.*`. All publishing is a no-op if the engine
was constructed without an event bus -- see service.py.
"""

RUN_STARTED = "workflow_engine.run.started"
RUN_COMPLETED = "workflow_engine.run.completed"
RUN_FAILED = "workflow_engine.run.failed"

STEP_STARTED = "workflow_engine.step.started"
STEP_COMPLETED = "workflow_engine.step.completed"
STEP_FAILED = "workflow_engine.step.failed"
STEP_SKIPPED = "workflow_engine.step.skipped"
STEP_RETRY_SCHEDULED = "workflow_engine.step.retry_scheduled"
STEP_APPROVAL_REQUESTED = "workflow_engine.step.approval_requested"
STEP_APPROVAL_DECIDED = "workflow_engine.step.approval_decided"
