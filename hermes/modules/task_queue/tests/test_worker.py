import asyncio

from hermes.core.supervisor.policy import RetryPolicy
from hermes.modules.task_queue.interface import build_task_queue, build_worker
from hermes.modules.task_queue.tests.fakes import FakeHeartbeatReporter, FakeTaskExecutor


async def test_run_once_returns_false_when_queue_is_empty(queue):
    worker = build_worker(worker_id="w1", queue=queue, executor=FakeTaskExecutor())

    claimed = await worker.run_once()

    assert claimed is False


async def test_run_once_executes_and_completes_a_task(queue):
    executor = FakeTaskExecutor(outcomes=["completed"])
    worker = build_worker(worker_id="w1", queue=queue, executor=executor)
    task = await queue.enqueue(kind="x", payload={"a": 1})

    claimed = await worker.run_once()

    assert claimed is True
    assert len(executor.executed_tasks) == 1
    updated = await queue.get_task(task.id)
    assert updated.status == "completed"


async def test_run_once_fails_the_task_when_executor_reports_failure(queue):
    executor = FakeTaskExecutor(outcomes=["failed"])
    worker = build_worker(worker_id="w1", queue=queue, executor=executor)
    task = await queue.enqueue(kind="x")

    await worker.run_once()

    updated = await queue.get_task(task.id)
    assert updated.status in ("queued", "dead_letter")  # retried or exhausted, never left "claimed"


async def test_run_once_fails_the_task_when_executor_raises():
    q = build_task_queue()
    executor = FakeTaskExecutor(outcomes=["raise"])
    worker = build_worker(worker_id="w1", queue=q, executor=executor)
    task = await q.enqueue(kind="x", retry_policy=RetryPolicy(max_attempts=1, backoff_base_seconds=0))

    await worker.run_once()

    updated = await q.get_task(task.id)
    assert updated.status == "dead_letter"
    assert "scripted executor failure" in updated.error


async def test_run_once_reports_heartbeats_when_state_manager_configured(queue):
    heartbeats = FakeHeartbeatReporter()
    worker = build_worker(worker_id="w1", queue=queue, executor=FakeTaskExecutor(), state_manager=heartbeats)

    await worker.run_once()  # nothing queued -> idle
    await queue.enqueue(kind="x")
    await worker.run_once()  # something queued -> busy

    assert ("w1", "idle") in heartbeats.reports
    assert ("w1", "busy") in heartbeats.reports


async def test_start_and_stop_drive_the_background_loop(queue):
    executor = FakeTaskExecutor()
    worker = build_worker(worker_id="w1", queue=queue, executor=executor, poll_interval_seconds=0.01)
    await queue.enqueue(kind="x")

    await worker.start()
    for _ in range(50):
        if executor.executed_tasks:
            break
        await asyncio.sleep(0.01)
    await worker.stop()

    assert len(executor.executed_tasks) == 1
