from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from app import relay
from app.core.config import Settings
from app.db.connections import create_redis_client
from app.main import app
from app.models import StepRun, StepStatus, WorkflowRun, WorkflowStatus
from app.services.recovery import recover_abandoned_steps
from app.services.retries import schedule_retries
from app.services.runs import get_workflow_run
from app.workers.worker import run_once
from app.workflows import Workflow
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def runtime(database):
    redis = create_redis_client(Settings())
    key = f"relay:test:api:{uuid4().hex}"
    with (
        patch("app.main.create_database_engine", return_value=database),
        patch("app.main.create_redis_client", return_value=Mock()),
        TestClient(app) as client,
    ):
        yield client, redis, key
    redis.delete(key)
    redis.close()
    with Session(database) as session, session.begin():
        session.execute(delete(WorkflowRun))


def create(database, runtime, handler=None):
    _, redis, queue = runtime
    workflow = Workflow("api_demo")
    workflow.step("a", handler or (lambda ctx: {"value": 42}), retries=1)
    workflow.step("b", lambda ctx: ctx["step_outputs"]["a"], depends_on=["a"])
    run_id = relay.run(
        workflow, {"company": "Stripe"}, engine=database, redis=redis, queue_name=queue
    )
    return run_id, {workflow.name: workflow}


def test_list_pagination_filter_and_step_details(database, runtime):
    client, redis, queue = runtime
    empty = client.get("/workflows")
    assert empty.json() == []
    assert empty.headers["X-Total-Count"] == "0"
    first, registry = create(database, runtime)
    second, _ = create(database, runtime)
    assert run_once(database, redis, registry, queue_name=queue)
    rows = client.get("/workflows").json()
    assert [r["id"] for r in rows] == [str(second), str(first)]
    page = client.get("/workflows?limit=1&offset=1")
    assert page.json() == rows[1:]
    assert page.headers["X-Total-Count"] == "2"
    filtered = client.get("/workflows?status=RUNNING")
    assert filtered.json() == rows[1:]
    assert filtered.headers["X-Total-Count"] == "1"
    past_end = client.get("/workflows?offset=2")
    assert past_end.json() == []
    assert past_end.headers["X-Total-Count"] == "2"
    assert client.get("/workflows?status=FAILED").headers["X-Total-Count"] == "0"
    detail = client.get(f"/workflows/{first}").json()
    assert detail["input"] == {"company": "Stripe"}
    assert detail["started_at"] and detail["completed_at"] is None
    a, b = detail["steps"]
    assert a["status"] == "COMPLETED" and a["output"] == {"value": 42}
    assert a["input"]["idempotency_key"] == f"{first}:a"
    assert a["attempt_count"] == 1 and a["max_attempts"] == 2
    assert a["lease_owner"] is None and a["error"] is None
    assert a["depends_on"] == [] and b["depends_on"] == [a["id"]]
    assert client.get(f"/workflows/{first}/steps").json() == detail["steps"]


@pytest.mark.parametrize("path", ["", "/steps", "/cancel"])
def test_missing_and_invalid_ids(runtime, path):
    client, _, _ = runtime
    request = client.post if path == "/cancel" else client.get
    assert request(f"/workflows/{uuid4()}{path}").status_code == 404
    assert request(f"/workflows/not-a-uuid{path}").status_code == 422


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "status=oops"])
def test_invalid_list_parameters(runtime, query):
    assert runtime[0].get(f"/workflows?{query}").status_code == 422


def test_cancel_queued_work_is_repeatable_and_blocks_claims(database, runtime):
    client, redis, queue = runtime
    handler = Mock(return_value={})
    run_id, registry = create(database, runtime, handler)
    path = f"/workflows/{run_id}/cancel"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(path), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()["status"] == "CANCELLED"
    assert responses[0].json()["completed_at"]
    assert not run_once(database, redis, registry, queue_name=queue)
    handler.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_cancel_during_handler_blocks_downstream_and_retry(database, runtime, fails):
    client, redis, queue = runtime
    started, release = Event(), Event()

    def handler(ctx):
        started.set()
        assert release.wait(10)
        if fails:
            raise RuntimeError("after cancellation")
        return {"finished": True}

    run_id, registry = create(database, runtime, handler)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_once, database, redis, registry, queue_name=queue)
        try:
            assert started.wait(10)
            response = client.post(f"/workflows/{run_id}/cancel")
            assert response.status_code == 200
        finally:
            release.set()
        assert future.result(timeout=10)
    detail = client.get(f"/workflows/{run_id}").json()
    assert detail["status"] == "CANCELLED"
    assert detail["completed_at"] == response.json()["completed_at"]
    assert detail["steps"][1]["status"] == "PENDING"
    assert detail["steps"][0]["status"] == ("RETRYING" if fails else "COMPLETED")
    assert (
        schedule_retries(
            database,
            redis,
            queue_name=queue,
            now=datetime.now(timezone.utc) + timedelta(days=1),
        )
        == 0
    )
    assert redis.llen(queue) == 0


@pytest.mark.parametrize("status", [WorkflowStatus.COMPLETED, WorkflowStatus.FAILED])
def test_terminal_runs_reject_cancellation(database, runtime, status):
    client, _, _ = runtime
    run_id, _ = create(database, runtime)
    with Session(database) as session, session.begin():
        session.get(WorkflowRun, run_id).status = status
    assert client.post(f"/workflows/{run_id}/cancel").status_code == 409
    assert client.get(f"/workflows/{run_id}").json()["status"] == status.value


def test_cancel_blocks_abandoned_step_recovery(database, runtime):
    client, redis, queue = runtime
    run_id, _ = create(database, runtime)
    with Session(database) as session, session.begin():
        run = get_workflow_run(session, run_id)
        run.status = WorkflowStatus.RUNNING
        step = run.steps[0]
        step_id = step.id
        step.status = StepStatus.RUNNING
        step.attempt_count = 1
        step.lease_owner = "dead-worker"
        step.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    redis.delete(queue)
    assert client.post(f"/workflows/{run_id}/cancel").status_code == 200
    assert recover_abandoned_steps(database, redis, queue_name=queue) == 0
    with Session(database) as session:
        assert session.get(WorkflowRun, run_id).status == WorkflowStatus.CANCELLED
        assert session.get(StepRun, step_id).lease_owner is None
    assert redis.llen(queue) == 0
