import os
import subprocess
import sys
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.db.connections import create_redis_client
from app.models import WorkflowStatus
from app.services.retries import schedule_retries
from app.services.runs import get_workflow_run
from sqlalchemy.orm import Session


@pytest.fixture
def runtime(database):
    client = create_redis_client(Settings())
    key = f"relay:test:examples:{uuid4().hex}"
    env = {
        **os.environ,
        "DATABASE_URL": database.url.render_as_string(hide_password=False),
    }
    try:
        yield client, key, env
    finally:
        client.delete(key)
        client.close()


def command(runtime, module, *args):
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        env=runtime[2],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )


def submit(runtime, example):
    return UUID(
        command(
            runtime,
            "app.examples",
            example,
            "--company",
            " Stripe ",
            "--queue",
            runtime[1],
        ).stdout.strip()
    )


def work(runtime):
    command(runtime, "app.workers.worker", "--once", "--queue", runtime[1])


@pytest.mark.integration
@pytest.mark.parametrize(
    "example,name,steps",
    [
        ("linear", "linear_example", 3),
        ("parallel", "parallel", 4),
        ("retry", "retry_example", 2),
        ("crash", "crash_recovery", 3),
    ],
)
def test_cli_submits_registered_examples(database, runtime, example, name, steps):
    run_id = submit(runtime, example)
    with Session(database) as session:
        run = get_workflow_run(session, run_id)
        assert run.workflow_name == name
        assert len(run.steps) == steps
        assert run.status == WorkflowStatus.PENDING
        assert run.input["company"] == " Stripe "
    assert runtime[0].llen(runtime[1]) == 1


@pytest.mark.integration
def test_linear_example_finishes_in_fresh_worker_processes(database, runtime):
    run_id = submit(runtime, "linear")
    for _ in range(3):
        work(runtime)
    with Session(database) as session:
        run = get_workflow_run(session, run_id)
        assert run.status == WorkflowStatus.COMPLETED
        assert next(s for s in run.steps if s.step_name == "save").output == {
            "record": {"company": "STRIPE"}
        }


@pytest.mark.integration
def test_retry_example_fails_twice_across_processes_then_finishes(database, runtime):
    run_id = submit(runtime, "retry")
    counter = f"relay:example:retry:{run_id}:request_company"
    try:
        for attempt in (1, 2):
            work(runtime)
            with Session(database) as session:
                run = get_workflow_run(session, run_id)
                step = next(s for s in run.steps if s.step_name == "request_company")
                assert step.attempt_count == attempt
                assert f"request {attempt}/3" in step.error
                due = step.next_retry_at
                assert due is not None
            assert (
                schedule_retries(database, runtime[0], queue_name=runtime[1], now=due)
                == 1
            )
        work(runtime)
        work(runtime)
        with Session(database) as session:
            run = get_workflow_run(session, run_id)
            assert run.status == WorkflowStatus.COMPLETED
            step = next(s for s in run.steps if s.step_name == "request_company")
            assert step.attempt_count == 3 and step.output["requests"] == 3
            assert step.error is None
        assert 0 < runtime[0].ttl(counter) <= 86400
    finally:
        runtime[0].delete(counter)


@pytest.mark.parametrize("seconds", ["0", "-1", "nan", "inf", "oops"])
def test_cli_rejects_invalid_duration_before_connecting(seconds):
    result = subprocess.run(
        [sys.executable, "-m", "app.examples", "crash", "--seconds", seconds],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert "seconds must be" in result.stderr
