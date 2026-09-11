import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from app import relay
from app.core.config import Settings
from app.db.connections import create_redis_client
from app.models import StepRun, StepStatus, WorkflowRun, WorkflowStatus
from app.services.queue import dequeue_step, enqueue_steps
from app.services.retries import retry_delay, schedule_retries
from app.services.runs import get_workflow_run
from app.workers.worker import run_once
from app.workflows import Workflow
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import delete
from sqlalchemy.orm import Session


@pytest.mark.parametrize(
    "attempt,delay",
    [(1, 1), (2, 2), (3, 4), (4, 8), (5, 16), (6, 32), (7, 60), (10000, 60)],
)
def test_exponential_delay_is_capped(attempt, delay):
    assert retry_delay(attempt) == delay


@pytest.fixture
def queue(database):
    client = create_redis_client(Settings())
    key = f"relay:test:retries:{uuid4().hex}"
    try:
        yield client, key
    finally:
        client.delete(key)
        client.close()
        # This module owns a dedicated temporary database. Remove its runs so
        # one test's due retries cannot be picked up by another test's scheduler.
        with Session(database) as session, session.begin():
            session.execute(delete(WorkflowRun))


def create_retry(database, queue, handler=None, retries=2):
    client, key = queue
    workflow = Workflow("retry")
    workflow.step(
        "a", handler or Mock(side_effect=RuntimeError("temporary")), retries=retries
    )
    workflow.step("b", lambda ctx: ctx["step_outputs"]["a"], depends_on=["a"])
    run_id = relay.run(workflow, {}, engine=database, redis=client, queue_name=key)
    registry = {workflow.name: workflow}
    assert run_once(database, client, registry, queue_name=key)
    with Session(database) as session:
        step = next(
            step
            for step in get_workflow_run(session, run_id).steps
            if step.step_name == "a"
        )
    return run_id, step.id, step.next_retry_at, registry


@pytest.mark.integration
def test_retry_deadlines_and_success_after_two_failures(database, queue):
    client, key = queue
    handler = Mock(side_effect=[RuntimeError("one"), RuntimeError("two"), {"ok": True}])
    before = datetime.now(timezone.utc)
    run_id, step_id, due, registry = create_retry(database, queue, handler)
    assert (
        before + timedelta(seconds=1)
        <= due
        <= datetime.now(timezone.utc) + timedelta(seconds=1)
    )
    assert (
        schedule_retries(
            database, client, queue_name=key, now=due - timedelta(microseconds=1)
        )
        == 0
    )
    assert schedule_retries(database, client, queue_name=key, now=due) == 1
    second_start = datetime.now(timezone.utc)
    assert run_once(database, client, registry, queue_name=key)
    with Session(database) as session:
        step = session.get(StepRun, step_id)
        due = step.next_retry_at
        assert step.status == StepStatus.RETRYING and step.attempt_count == 2
        assert due >= second_start + timedelta(seconds=2)
        assert step.error == "RuntimeError: two"
    assert schedule_retries(database, client, queue_name=key, now=due) == 1
    assert run_once(database, client, registry, queue_name=key)
    assert run_once(database, client, registry, queue_name=key)
    with Session(database) as session:
        run = get_workflow_run(session, run_id)
        assert all(step.status == StepStatus.COMPLETED for step in run.steps)
        step = session.get(StepRun, step_id)
        assert (
            step.attempt_count == 3
            and step.error is None
            and step.next_retry_at is None
        )
    assert handler.call_count == 3


@pytest.mark.integration
def test_exhausted_retries_fail_workflow_and_block_dependents(database, queue):
    client, key = queue
    run_id, step_id, due, registry = create_retry(database, queue, retries=1)
    assert schedule_retries(database, client, queue_name=key, now=due) == 1
    assert run_once(database, client, registry, queue_name=key)
    with Session(database) as session:
        run = get_workflow_run(session, run_id)
        step = session.get(StepRun, step_id)
        assert run.status == WorkflowStatus.FAILED and run.completed_at is not None
        assert step.status == StepStatus.FAILED and step.attempt_count == 2
        assert step.next_retry_at is None
        assert (
            next(step for step in run.steps if step.step_name == "b").status
            == StepStatus.PENDING
        )
    assert (
        schedule_retries(database, client, queue_name=key, now=due + timedelta(days=1))
        == 0
    )
    assert client.llen(key) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    "status",
    [WorkflowStatus.FAILED, WorkflowStatus.CANCELLED, WorkflowStatus.COMPLETED],
)
def test_scheduler_skips_terminal_workflows(database, queue, status):
    client, key = queue
    run_id, _, due, _ = create_retry(database, queue)
    with Session(database) as session, session.begin():
        session.get(WorkflowRun, run_id).status = status
    assert schedule_retries(database, client, queue_name=key, now=due) == 0


@pytest.mark.integration
def test_concurrent_scans_queue_a_retry_only_once(database, queue):
    client, key = queue
    _, step_id, due, _ = create_retry(database, queue)
    barrier = Barrier(2)

    def scan():
        barrier.wait(timeout=10)
        return schedule_retries(database, client, queue_name=key, now=due)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(scan) for _ in range(2)]
        assert sorted(future.result(timeout=15) for future in futures) == [0, 1]
    assert dequeue_step(client, queue_name=key) == step_id
    assert dequeue_step(client, queue_name=key) is None


@pytest.mark.integration
def test_fresh_scheduler_process_reads_persisted_retry_time(database, queue):
    client, key = queue
    _, step_id, _, _ = create_retry(database, queue)
    with Session(database) as session, session.begin():
        session.get(StepRun, step_id).next_retry_at = datetime.now(
            timezone.utc
        ) - timedelta(seconds=1)
    env = {
        **os.environ,
        "DATABASE_URL": database.url.render_as_string(hide_password=False),
        "REDIS_URL": Settings().redis_url,
    }
    result = subprocess.run(
        [sys.executable, "-m", "app.workers.scheduler", "--once", "--queue", key],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert dequeue_step(client, queue_name=key) == step_id
    with Session(database) as session:
        step = session.get(StepRun, step_id)
        assert step.status == StepStatus.READY and step.attempt_count == 1
        assert step.next_retry_at is None


@pytest.mark.integration
def test_scheduler_dispatch_error_keeps_saved_retry_available(database, queue):
    client, key = queue
    run_id, step_id, due, _ = create_retry(database, queue)
    with (
        patch.object(client, "rpush", side_effect=RedisConnectionError("offline")),
        pytest.raises(relay.QueueDispatchError) as error,
    ):
        schedule_retries(database, client, queue_name=key, now=due)
    assert error.value.run_id == run_id and error.value.step_run_ids == (step_id,)
    with Session(database) as session:
        assert session.get(StepRun, step_id).status == StepStatus.READY
    enqueue_steps(client, error.value.step_run_ids, queue_name=key)
    assert dequeue_step(client, queue_name=key) == step_id
