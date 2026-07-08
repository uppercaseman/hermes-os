"""Tests for the two identity guarantees this bridge depends on: the
enqueued task's id must equal Commander's DispatchedTask.id, and
re-dispatching the same DispatchedTask (Commander's own task-level
retry can do this) must never reset an already-in-flight task.
"""
import uuid

from hermes.core.commander.models import DispatchedTask
from hermes.modules.task_queue.commander_dispatcher import TaskQueueDispatcher
from hermes.modules.task_queue.interface import build_task_queue


def _dispatched_task(**overrides) -> DispatchedTask:
    defaults = dict(correlation_id=uuid.uuid4(), kind="workflow_step", payload={"step": "wf1"})
    defaults.update(overrides)
    return DispatchedTask(**defaults)


async def test_dispatch_enqueues_a_task_with_the_same_id():
    queue = build_task_queue()
    dispatcher = TaskQueueDispatcher(queue=queue)
    task = _dispatched_task()

    await dispatcher.dispatch(task)

    enqueued = await queue.get_task(task.id)
    assert enqueued.id == task.id
    assert enqueued.payload == {"step": "wf1"}


async def test_dispatch_sets_mission_id_from_correlation_id_by_convention():
    queue = build_task_queue()
    dispatcher = TaskQueueDispatcher(queue=queue)
    task = _dispatched_task()

    await dispatcher.dispatch(task)

    enqueued = await queue.get_task(task.id)
    assert enqueued.mission_id == task.correlation_id


async def test_re_dispatching_the_same_task_does_not_reset_its_state():
    """Simulates Commander's own task-level retry calling dispatch()
    again for the identical DispatchedTask (same id) because a
    completion event hadn't arrived yet."""
    queue = build_task_queue()
    dispatcher = TaskQueueDispatcher(queue=queue)
    task = _dispatched_task()

    await dispatcher.dispatch(task)
    await queue.claim_next("worker-1")  # the task is now in-flight

    await dispatcher.dispatch(task)  # Commander retries the SAME task

    still_in_flight = await queue.get_task(task.id)
    assert still_in_flight.status == "claimed"  # not reset back to "queued"
