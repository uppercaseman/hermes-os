import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from hermes.core.supervisor.policy import RetryPolicy
from hermes.modules.task_queue.errors import InvalidTaskStateError, UnknownTaskError
from hermes.modules.task_queue.events import (
    TASK_CLAIMED,
    TASK_COMPLETED,
    TASK_DEAD_LETTERED,
    TASK_ENQUEUED,
    TASK_FAILED,
    TASK_RECOVERED,
    TASK_RETRY_SCHEDULED,
)
from hermes.modules.task_queue.interface import build_task_queue

INSTANT_RETRY = RetryPolicy(max_attempts=3, backoff_base_seconds=0, backoff_multiplier=1)


# --------------------------------------------------------------------- #
# Creating / persisting tasks (#1, #2)
# --------------------------------------------------------------------- #

async def test_enqueue_creates_a_queued_task(queue):
    task = await queue.enqueue(kind="research", payload={"topic": "x"})

    assert task.status == "queued"
    assert (await queue.get_task(task.id)).kind == "research"


async def test_get_unknown_task_raises(queue):
    with pytest.raises(UnknownTaskError):
        await queue.get_task(uuid.uuid4())


# --------------------------------------------------------------------- #
# Explicit id + idempotency -- the correctness guarantees the Commander
# bridge depends on (#9, and the Commander re-dispatch safety property)
# --------------------------------------------------------------------- #

async def test_enqueue_with_explicit_id_uses_it(queue):
    fixed_id = uuid.uuid4()

    task = await queue.enqueue(id=fixed_id, kind="x")

    assert task.id == fixed_id


async def test_re_enqueue_with_the_same_explicit_id_is_a_no_op(queue):
    """The exact safety property TaskQueueDispatcher relies on: Commander
    can call dispatch() again for the same DispatchedTask (same id) and
    must never reset an already-in-flight task's state."""
    fixed_id = uuid.uuid4()
    first = await queue.enqueue(id=fixed_id, kind="x", payload={"v": 1})
    await queue.claim_next("worker-1")  # advance its state past "queued"

    second = await queue.enqueue(id=fixed_id, kind="x", payload={"v": 2})

    assert second.status == "claimed"  # unchanged by the second enqueue call
    assert second.payload == {"v": 1}  # NOT overwritten with the new payload


async def test_idempotency_key_returns_the_existing_task(queue):
    first = await queue.enqueue(kind="x", idempotency_key="op-123")
    second = await queue.enqueue(kind="x", idempotency_key="op-123", payload={"different": True})

    assert first.id == second.id
    assert second.payload == {}  # the original, not the second call's payload


async def test_different_idempotency_keys_create_separate_tasks(queue):
    a = await queue.enqueue(kind="x", idempotency_key="a")
    b = await queue.enqueue(kind="x", idempotency_key="b")

    assert a.id != b.id


# --------------------------------------------------------------------- #
# Claiming / status updates / worker assignment (#3, #8)
# --------------------------------------------------------------------- #

async def test_claim_next_assigns_the_worker_and_marks_claimed(queue):
    await queue.enqueue(kind="x")

    claimed = await queue.claim_next("worker-1")

    assert claimed is not None
    assert claimed.status == "claimed"
    assert claimed.claimed_by == "worker-1"


async def test_claim_next_returns_none_when_nothing_eligible(queue):
    assert await queue.claim_next("worker-1") is None


async def test_claimed_task_is_not_claimable_by_a_second_worker(queue):
    await queue.enqueue(kind="x")
    await queue.claim_next("worker-1")

    assert await queue.claim_next("worker-2") is None


async def test_complete_marks_task_completed_with_output(queue):
    task = await queue.enqueue(kind="x")
    await queue.claim_next("w1")

    completed = await queue.complete(task.id, output={"result": 42})

    assert completed.status == "completed"
    assert completed.output == {"result": 42}


async def test_complete_an_unclaimed_task_raises(queue):
    task = await queue.enqueue(kind="x")

    with pytest.raises(InvalidTaskStateError):
        await queue.complete(task.id)


# --------------------------------------------------------------------- #
# Retries + dead-letter queue (#4, #10)
# --------------------------------------------------------------------- #

async def test_fail_with_retries_remaining_requeues_the_task(queue):
    task = await queue.enqueue(kind="x", retry_policy=INSTANT_RETRY)
    await queue.claim_next("w1")

    failed = await queue.fail(task.id, error="transient")

    assert failed.status == "queued"
    assert failed.attempts == 1
    reclaimed = await queue.claim_next("w1")
    assert reclaimed.id == task.id


async def test_fail_exhausting_retries_dead_letters_the_task(queue):
    task = await queue.enqueue(kind="x", retry_policy=RetryPolicy(max_attempts=1, backoff_base_seconds=0))
    await queue.claim_next("w1")

    failed = await queue.fail(task.id, error="permanent")

    assert failed.status == "dead_letter"
    assert task.id in {t.id for t in await queue.list_dead_letter_tasks()}


async def test_fail_an_unclaimed_task_raises(queue):
    task = await queue.enqueue(kind="x")

    with pytest.raises(InvalidTaskStateError):
        await queue.fail(task.id, error="x")


# --------------------------------------------------------------------- #
# Scheduling future tasks (#5)
# --------------------------------------------------------------------- #

async def test_scheduled_future_task_is_not_yet_claimable(queue):
    await queue.enqueue(kind="x", scheduled_for=datetime.now(timezone.utc) + timedelta(hours=1))

    assert await queue.claim_next("w1") is None


async def test_task_becomes_claimable_once_scheduled_time_passes(queue):
    await queue.enqueue(kind="x", scheduled_for=datetime.now(timezone.utc) - timedelta(seconds=1))

    assert await queue.claim_next("w1") is not None


# --------------------------------------------------------------------- #
# Task dependencies (#6)
# --------------------------------------------------------------------- #

async def test_dependent_task_is_not_claimable_until_dependency_completes(queue):
    dep = await queue.enqueue(kind="dep")
    dependent = await queue.enqueue(kind="dependent", depends_on=[dep.id])

    ready = await queue.claim_next("w1")
    assert ready.id == dep.id  # only the dependency is eligible so far

    assert await queue.claim_next("w1") is None  # dependent still blocked

    await queue.complete(dep.id)
    now_ready = await queue.claim_next("w1")
    assert now_ready.id == dependent.id


async def test_dependency_that_dead_letters_cascades_to_the_dependent(queue):
    dep = await queue.enqueue(kind="dep", retry_policy=RetryPolicy(max_attempts=1, backoff_base_seconds=0))
    dependent = await queue.enqueue(kind="dependent", depends_on=[dep.id])

    claimed_dep = await queue.claim_next("w1")
    await queue.fail(claimed_dep.id, error="permanent")  # dep -> dead_letter

    await queue.claim_next("w1")  # triggers the lazy dependency check for `dependent`

    updated = await queue.get_task(dependent.id)
    assert updated.status == "dead_letter"


# --------------------------------------------------------------------- #
# Task priorities (#7)
# --------------------------------------------------------------------- #

async def test_lower_priority_number_is_claimed_first(queue):
    await queue.enqueue(kind="low", priority=50)
    await queue.enqueue(kind="high", priority=1)

    first = await queue.claim_next("w1")

    assert first.kind == "high"


# --------------------------------------------------------------------- #
# Crash recovery (#11)
# --------------------------------------------------------------------- #

async def test_recover_expired_claims_requeues_a_stale_claim(queue):
    task = await queue.enqueue(kind="x", retry_policy=INSTANT_RETRY)
    await queue.claim_next("w1")

    await asyncio.sleep(0.1)  # past the fixture's 0.05s visibility timeout
    recovered_count = await queue.recover_expired_claims()

    assert recovered_count == 1
    updated = await queue.get_task(task.id)
    assert updated.status == "queued"
    assert updated.claim_attempts == 1


async def test_recover_expired_claims_dead_letters_after_max_claim_attempts(queue):
    """queue fixture has max_claim_attempts=2."""
    task = await queue.enqueue(kind="x")
    for _ in range(2):
        await queue.claim_next("w1")
        await asyncio.sleep(0.1)
        await queue.recover_expired_claims()

    updated = await queue.get_task(task.id)
    assert updated.status == "dead_letter"


async def test_recover_expired_claims_is_a_no_op_when_nothing_is_stale(queue):
    await queue.enqueue(kind="x")  # never claimed -- nothing to recover

    assert await queue.recover_expired_claims() == 0


# --------------------------------------------------------------------- #
# Mission-level and workflow-level tracking (#13, #14)
# --------------------------------------------------------------------- #

async def test_list_tasks_for_mission(queue):
    mission_id = uuid.uuid4()
    a = await queue.enqueue(kind="x", mission_id=mission_id)
    await queue.enqueue(kind="x")  # unrelated task

    tasks = await queue.list_tasks_for_mission(mission_id)

    assert [t.id for t in tasks] == [a.id]


async def test_set_workflow_run_id_then_query_by_it(queue):
    task = await queue.enqueue(kind="x")
    run_id = uuid.uuid4()

    updated = await queue.set_workflow_run_id(task.id, run_id)
    assert updated.workflow_run_id == run_id

    tasks = await queue.list_tasks_for_workflow_run(run_id)
    assert [t.id for t in tasks] == [task.id]


# --------------------------------------------------------------------- #
# Event publishing (#12)
# --------------------------------------------------------------------- #

async def test_events_published_across_the_task_lifecycle(bus):
    queue = build_task_queue(event_bus=bus, visibility_timeout_seconds=0.05, max_claim_attempts=1)
    seen = []

    async def capture(event):
        seen.append(event.event_type)

    await bus.subscribe("*", capture)

    task = await queue.enqueue(kind="x", retry_policy=RetryPolicy(max_attempts=1, backoff_base_seconds=0))
    await queue.claim_next("w1")
    await queue.fail(task.id, error="boom")  # exhausts retries -> dead-lettered

    assert TASK_ENQUEUED in seen
    assert TASK_CLAIMED in seen
    assert TASK_DEAD_LETTERED in seen
    assert TASK_FAILED in seen  # the exact string Commander listens for


async def test_retry_and_completion_events(bus):
    queue = build_task_queue(event_bus=bus, visibility_timeout_seconds=0.05)
    seen = []

    async def capture(event):
        seen.append(event.event_type)

    await bus.subscribe("*", capture)

    task = await queue.enqueue(kind="x", retry_policy=INSTANT_RETRY)
    await queue.claim_next("w1")
    await queue.fail(task.id, error="transient")
    await queue.claim_next("w1")
    await queue.complete(task.id)

    assert TASK_RETRY_SCHEDULED in seen
    assert TASK_COMPLETED in seen


async def test_recovery_publishes_recovered_event(bus):
    queue = build_task_queue(event_bus=bus, visibility_timeout_seconds=0.05, max_claim_attempts=5)
    seen = []

    async def capture(event):
        seen.append(event.event_type)

    await bus.subscribe("*", capture)

    await queue.enqueue(kind="x")
    await queue.claim_next("w1")
    await asyncio.sleep(0.1)
    await queue.recover_expired_claims()

    assert TASK_RECOVERED in seen


async def test_works_fully_standalone_without_an_event_bus(queue):
    task = await queue.enqueue(kind="x")
    await queue.claim_next("w1")
    await queue.complete(task.id)  # must not raise despite no event bus configured
