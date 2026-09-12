from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import pytest
from app import relay
from app.core.config import Settings
from app.db.connections import create_redis_client
from app.models import StepRun, StepStatus, WorkflowStatus
from app.services.dispatch import reconcile_ready_steps
from app.services.execution import execute_step
from app.services.queue import dequeue_step
from app.services.runs import get_workflow_run
from app.workflows import Workflow
from redis.exceptions import ConnectionError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def queue():
    client = create_redis_client(Settings())
    key = f"relay:test:dispatch:{uuid4().hex}"
    try:
        yield client, key
    finally:
        client.delete(key)
        client.close()


def test_failed_creation_and_lost_pop_recover_without_duplicate_execution(
    database, queue
):
    client, key = queue
    workflow = Workflow("dispatch")
    workflow.step("work", lambda ctx: {"done": True})
    with (
        patch.object(client, "rpush", side_effect=ConnectionError()),
        pytest.raises(relay.QueueDispatchError) as error,
    ):
        relay.run(workflow, {}, engine=database, redis=client, queue_name=key)
    assert reconcile_ready_steps(database, client, queue_name=key) == 1
    step_id = dequeue_step(client, queue_name=key)
    # Simulate death after pop but before database claim.
    assert reconcile_ready_steps(database, client, queue_name=key) == 1
    assert dequeue_step(client, queue_name=key) == step_id
    assert execute_step(
        database, step_id, {workflow.name: workflow}, redis=client, queue_name=key
    )
    assert not execute_step(
        database, step_id, {workflow.name: workflow}, redis=client, queue_name=key
    )
    assert reconcile_ready_steps(database, client, queue_name=key) == 0
    with Session(database) as session:
        run = get_workflow_run(session, error.value.run_id)
        assert run.status == WorkflowStatus.COMPLETED
        assert run.steps[0].attempt_count == 1


def test_concurrent_scans_cover_multiple_batches_without_queue_growth(database, queue):
    client, key = queue
    workflow = Workflow("many_ready")
    for i in range(205):
        workflow.step(f"work_{i}", lambda ctx: {})
    relay.run(workflow, {}, engine=database, redis=client, queue_name=key)
    client.delete(key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: reconcile_ready_steps(database, client, queue_name=key),
                range(2),
            )
        )
    assert sum(results) == 205
    assert client.llen(key) == 205
    assert reconcile_ready_steps(database, client, queue_name=key) == 0
    assert len(set(client.lrange(key, 0, -1))) == 205


@pytest.mark.parametrize(
    "status",
    [WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED],
)
def test_terminal_runs_are_not_redelivered(database, queue, status):
    client, key = queue
    workflow = Workflow("terminal")
    workflow.step("work", lambda ctx: {})
    run_id = relay.run(workflow, {}, engine=database, redis=client, queue_name=key)
    client.delete(key)
    with Session(database) as session, session.begin():
        get_workflow_run(session, run_id).status = status
    assert reconcile_ready_steps(database, client, queue_name=key) == 0


def test_queue_routing_and_pending_steps(database, queue):
    client, key = queue
    workflow = Workflow("routing")
    workflow.step("root", lambda ctx: {})
    workflow.step("child", lambda ctx: {}, depends_on=["root"])
    run_id = relay.run(workflow, {}, engine=database, redis=client, queue_name=key)
    client.delete(key)
    assert reconcile_ready_steps(database, client, queue_name=key + ":other") == 0
    assert reconcile_ready_steps(database, client, queue_name=key) == 1
    root = dequeue_step(client, queue_name=key)
    # A failed downstream dispatch is also recovered, without rerunning the root.
    with (
        patch.object(client, "rpush", side_effect=ConnectionError()),
        pytest.raises(relay.QueueDispatchError),
    ):
        execute_step(
            database, root, {workflow.name: workflow}, redis=client, queue_name=key
        )
    assert reconcile_ready_steps(database, client, queue_name=key) == 1
    child = dequeue_step(client, queue_name=key)
    with Session(database) as session:
        assert session.get(StepRun, root).status == StepStatus.COMPLETED
        assert session.get(StepRun, child).attempt_count == 0
        assert get_workflow_run(session, run_id).queue_name == key
