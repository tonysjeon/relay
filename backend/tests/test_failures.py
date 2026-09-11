from unittest.mock import Mock
from uuid import uuid4

import pytest
from app import relay
from app.core.config import Settings
from app.db.connections import create_redis_client
from app.models import StepStatus, WorkflowStatus
from app.services.runs import get_workflow_run
from app.workers.worker import run_once
from app.workflows import Workflow
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def queue():
    client = create_redis_client(Settings())
    key = f"relay:test:failures:{uuid4().hex}"
    try:
        yield client, key
    finally:
        client.delete(key)
        client.close()


@pytest.mark.parametrize("retries", [0, 2])
def test_failure_records_attempt_and_blocks_dependents(database, queue, retries):
    client, key = queue
    workflow = Workflow("failure")
    workflow.step(
        "a", Mock(side_effect=RuntimeError("temporary problem")), retries=retries
    )
    workflow.step("b", Mock(), depends_on=["a"])
    run_id = relay.run(workflow, {}, engine=database, redis=client, queue_name=key)
    assert run_once(database, client, {workflow.name: workflow}, queue_name=key)
    with Session(database) as session:
        run = get_workflow_run(session, run_id)
        steps = {step.step_name: step for step in run.steps}
        assert steps["a"].status == (
            StepStatus.RETRYING if retries else StepStatus.FAILED
        )
        assert steps["a"].attempt_count == 1
        assert steps["a"].error == "RuntimeError: temporary problem"
        assert steps["b"].status == StepStatus.PENDING
        assert steps["b"].attempt_count == 0
        assert run.status == (
            WorkflowStatus.RUNNING if retries else WorkflowStatus.FAILED
        )
    assert client.llen(key) == 0


def test_same_worker_process_can_continue_after_handler_failure(database, queue):
    client, key = queue
    bad = Workflow("bad")
    bad.step("work", Mock(side_effect=ValueError("bad input")))
    good = Workflow("good")
    good.step("work", lambda ctx: {"ok": True})
    relay.run(bad, {}, engine=database, redis=client, queue_name=key)
    good_id = relay.run(good, {}, engine=database, redis=client, queue_name=key)
    registry = {bad.name: bad, good.name: good}
    assert run_once(database, client, registry, queue_name=key)
    assert run_once(database, client, registry, queue_name=key)
    with Session(database) as session:
        assert get_workflow_run(session, good_id).steps[0].output == {"ok": True}
