import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import pytest
from app import relay
from app.core.config import Settings
from app.db.connections import create_redis_client
from app.models import StepRun, StepStatus, Worker, WorkflowRun, WorkflowStatus
from app.services.failures import record_failure
from app.services.queue import dequeue_step, enqueue_steps
from app.services.recovery import recover_abandoned_steps
from app.services.runs import get_workflow_run
from app.services.workers import list_workers
from app.workers.recovery_demo import recovery_workflow
from app.workers.worker import run_once
from app.workflows import Workflow
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import delete
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def queue(database):
    client = create_redis_client(Settings())
    key = f"relay:test:recovery:{uuid4().hex}"
    try:
        yield client, key
    finally:
        client.delete(key)
        client.close()
        # Runs belong to this module's temporary database, never the application DB.
        with Session(database) as session, session.begin():
            session.execute(delete(WorkflowRun))


def abandon(database, queue, *, attempts=1, expired=True):
    client, key = queue
    workflow = Workflow("abandoned")
    workflow.step("work", lambda ctx: "recovered", retries=1)
    workflow.step(
        "report", lambda ctx: ctx["step_outputs"]["work"], depends_on=["work"]
    )
    run_id = relay.run(workflow, {}, engine=database, redis=client, queue_name=key)
    step_id = dequeue_step(client, queue_name=key)
    with Session(database) as session, session.begin():
        step = session.get(StepRun, step_id)
        step.status, step.attempt_count, step.lease_owner = (
            StepStatus.RUNNING,
            attempts,
            "dead-worker",
        )
        step.started_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        step.lease_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=-1 if expired else 30
        )
        session.get(WorkflowRun, run_id).status = WorkflowStatus.RUNNING
    return run_id, step_id, {workflow.name: workflow}


def test_recovered_claim_consumes_one_more_attempt_and_finishes_workflow(
    database, queue
):
    client, key = queue
    run_id, step_id, registry = abandon(database, queue)
    assert recover_abandoned_steps(database, client, queue_name=key) == 1
    with Session(database) as session:
        step = session.get(StepRun, step_id)
        assert step.status == StepStatus.READY and step.attempt_count == 1
        assert step.lease_owner is None and step.lease_expires_at is None
        assert step.error == "Worker lease expired"
    assert (
        record_failure(
            database,
            run_id,
            step_id,
            RuntimeError("late"),
            worker_id="dead-worker",
            attempt=1,
        )
        is None
    )
    assert run_once(database, client, registry, queue_name=key, worker_id="replacement")
    assert run_once(database, client, registry, queue_name=key, worker_id="replacement")
    with Session(database) as session:
        run = get_workflow_run(session, run_id)
        assert run.status == WorkflowStatus.COMPLETED and run.completed_at is not None
        step = session.get(StepRun, step_id)
        assert (
            step.attempt_count == 2
            and step.output == "recovered"
            and step.error is None
        )


def test_exhausted_abandoned_step_fails_workflow(database, queue):
    client, key = queue
    run_id, step_id, _ = abandon(database, queue, attempts=2)
    assert recover_abandoned_steps(database, client, queue_name=key) == 0
    with Session(database) as session:
        run = get_workflow_run(session, run_id)
        step = session.get(StepRun, step_id)
        assert run.status == WorkflowStatus.FAILED and run.completed_at is not None
        assert step.status == StepStatus.FAILED and step.attempt_count == 2
        assert step.lease_owner is None and step.lease_expires_at is None
        assert (
            next(step for step in run.steps if step.step_name == "report").status
            == StepStatus.PENDING
        )
    assert client.llen(key) == 0


def test_unexpired_lease_is_not_recovered(database, queue):
    client, key = queue
    _, step_id, _ = abandon(database, queue, expired=False)
    assert recover_abandoned_steps(database, client, queue_name=key) == 0
    with Session(database) as session:
        assert session.get(StepRun, step_id).status == StepStatus.RUNNING


@pytest.mark.parametrize(
    "status",
    [WorkflowStatus.CANCELLED, WorkflowStatus.FAILED, WorkflowStatus.COMPLETED],
)
def test_terminal_workflow_is_not_resurrected(database, queue, status):
    client, key = queue
    run_id, step_id, _ = abandon(database, queue)
    with Session(database) as session, session.begin():
        session.get(WorkflowRun, run_id).status = status
    assert recover_abandoned_steps(database, client, queue_name=key) == 0
    with Session(database) as session:
        assert session.get(WorkflowRun, run_id).status == status
        assert session.get(StepRun, step_id).lease_owner is None
    assert client.llen(key) == 0


def test_concurrent_recovery_scans_dispatch_once(database, queue):
    client, key = queue
    _, step_id, _ = abandon(database, queue)
    barrier = Barrier(2)

    def scan():
        barrier.wait(timeout=5)
        return recover_abandoned_steps(database, client, queue_name=key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(scan) for _ in range(2)]
        assert sorted(f.result(timeout=10) for f in futures) == [0, 1]
    assert dequeue_step(client, queue_name=key) == step_id
    assert dequeue_step(client, queue_name=key) is None


def test_redis_failure_preserves_recovered_step_for_dispatch_retry(database, queue):
    client, key = queue
    run_id, step_id, _ = abandon(database, queue)
    with (
        patch.object(client, "rpush", side_effect=RedisConnectionError("offline")),
        pytest.raises(relay.QueueDispatchError) as error,
    ):
        recover_abandoned_steps(database, client, queue_name=key)
    assert error.value.run_id == run_id and error.value.step_run_ids == (step_id,)
    with Session(database) as session:
        assert session.get(StepRun, step_id).status == StepStatus.READY
    enqueue_steps(client, error.value.step_run_ids, queue_name=key)
    assert dequeue_step(client, queue_name=key) == step_id


def test_workflow_survives_killed_worker_process(database, queue, tmp_path):
    client, key = queue
    env = {
        **os.environ,
        "DATABASE_URL": database.url.render_as_string(hide_password=False),
        "REDIS_URL": Settings().redis_url,
        "STEP_LEASE_SECONDS": "1",
        "WORKER_HEARTBEAT_SECONDS": "0.1",
        "WORKER_TIMEOUT_SECONDS": "0.5",
        "LEASE_SCAN_INTERVAL_SECONDS": "0.1",
        "RETRY_SCAN_INTERVAL_SECONDS": "0.1",
    }
    processes, logs = [], []

    def start(module):
        log = (tmp_path / f"process-{len(processes)}.log").open("w+")
        logs.append(log)
        process = subprocess.Popen(
            [sys.executable, "-m", module, "--queue", key],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        processes.append(process)
        return process

    def run():
        with Session(database) as session:
            return get_workflow_run(session, run_id)

    def analysis():
        return next(step for step in run().steps if step.step_name == "long_analysis")

    def wait_for(condition, live):
        deadline = time.monotonic() + 20
        while not condition():
            assert all(process.poll() is None for process in live), (
                "Process exited unexpectedly"
            )
            assert time.monotonic() < deadline, "Timed out waiting for recovery"
            time.sleep(0.03)

    try:
        run_id = relay.run(
            recovery_workflow,
            {"company": "Stripe", "seconds": 4},
            engine=database,
            redis=client,
            queue_name=key,
        )
        first = start("app.workers.worker")
        wait_for(lambda: analysis().status == StepStatus.RUNNING, [first])
        original_owner = analysis().lease_owner
        original_expiry = analysis().lease_expires_at
        second = start("app.workers.worker")
        scheduler = start("app.workers.scheduler")
        wait_for(
            lambda: analysis().lease_expires_at > original_expiry,
            [first, second, scheduler],
        )
        assert analysis().attempt_count == 1  # Healthy renewal must prevent recovery.
        first.kill()
        first.wait(timeout=5)
        with Session(database) as session:
            stopped_heartbeat = session.get(Worker, original_owner).last_heartbeat
        wait_for(lambda: run().status == WorkflowStatus.COMPLETED, [second, scheduler])
        result = run()
        steps = {step.step_name: step for step in result.steps}
        assert steps["fetch"].attempt_count == 1
        assert steps["long_analysis"].attempt_count == 2
        assert steps["long_analysis"].output["pid"] == second.pid
        assert steps["report"].output == {"summary": "Stripe"}
        assert result.completed_at is not None
        assert all(step.lease_owner is None for step in steps.values())
        assert (
            next(
                row
                for row in list_workers(database, 0.5)
                if row["id"] == original_owner
            )["status"]
            == "unhealthy"
        )
        with Session(database) as session:
            assert (
                session.get(Worker, original_owner).last_heartbeat == stopped_heartbeat
            )
    finally:
        for process in processes:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.seek(0)
            print(log.read())
            log.close()
