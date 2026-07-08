from hermes.modules.task_queue.models import QueuedTask, TaskExecutionResult


def test_queued_task_defaults_to_queued_status():
    task = QueuedTask()

    assert task.status == "queued"
    assert task.attempts == 0
    assert task.claim_attempts == 0
    assert task.depends_on == []


def test_task_execution_result_output_defaults_to_none():
    result = TaskExecutionResult(status="completed")

    assert result.output is None
    assert result.error is None
