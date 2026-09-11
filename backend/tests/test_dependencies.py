import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from app import relay
from app.core.config import Settings
from app.db.connections import create_redis_client
from app.models import StepStatus
from app.services.queue import dequeue_step, enqueue_step, enqueue_steps
from app.services.runs import get_workflow_run
from app.workers.worker import run_once
from app.workflows import Workflow
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def queue():
    client = create_redis_client(Settings())
    key = f"relay:test:dependencies:{uuid4().hex}"
    try:
        yield client, key
    finally:
        client.delete(key)
        client.close()


def test_three_steps_execute_automatically(database, queue):
    client, key = queue
    workflow = Workflow("linear")
    workflow.step("a", lambda ctx: {"value": ctx["workflow_input"]["value"] + 1})
    workflow.step(
        "b",
        lambda ctx: {"value": ctx["step_outputs"]["a"]["value"] * 2},
        depends_on=["a"],
    )
    workflow.step(
        "c", lambda ctx: ctx["step_outputs"]["b"]["value"] + 3, depends_on=["b"]
    )
    run_id = relay.run(
        workflow, {"value": 4}, engine=database, redis=client, queue_name=key
    )
    registry = {workflow.name: workflow}
    for _ in range(3):
        assert run_once(database, client, registry, queue_name=key)
    assert not run_once(database, client, registry, queue_name=key)
    with Session(database) as session:
        run = get_workflow_run(session, run_id)
        assert all(step.status == StepStatus.COMPLETED for step in run.steps)
        assert all(step.attempt_count == 1 for step in run.steps)
        assert next(step.output for step in run.steps if step.step_name == "c") == 13


def make_join(database, queue, handler):
    client, key = queue
    workflow = Workflow("join")
    workflow.step("a", handler)
    workflow.step("b", handler)
    workflow.step("c", lambda ctx: sorted(ctx["step_outputs"]), depends_on=["a", "b"])
    run_id = relay.run(workflow, {}, engine=database, redis=client, queue_name=key)
    return workflow, run_id


def test_waits_for_every_dependency_and_queues_once(database, queue):
    client, key = queue
    workflow, run_id = make_join(database, queue, lambda ctx: "ok")
    registry = {workflow.name: workflow}
    assert run_once(database, client, registry, queue_name=key)
    with Session(database) as session:
        steps = {
            step.step_name: step for step in get_workflow_run(session, run_id).steps
        }
        assert steps["c"].status == StepStatus.PENDING
        first_id = steps["a"].id
        last_id = steps["c"].id
    assert client.llen(key) == 1
    assert run_once(database, client, registry, queue_name=key)
    assert [json.loads(message) for message in client.lrange(key, 0, -1)] == [
        {"step_run_id": str(last_id)}
    ]
    enqueue_step(client, first_id, queue_name=key)
    assert run_once(database, client, registry, queue_name=key)
    assert not run_once(database, client, registry, queue_name=key)
    assert client.llen(key) == 0


def test_simultaneous_completions_do_not_miss_or_duplicate_unlock(database, queue):
    client, key = queue
    barrier = Barrier(2)

    def handler(ctx):
        barrier.wait(timeout=10)
        return "ok"

    workflow, run_id = make_join(database, queue, handler)
    registry = {workflow.name: workflow}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(run_once, database, client, registry, queue_name=key)
            for _ in range(2)
        ]
        assert all(future.result(timeout=20) for future in futures)
    assert client.llen(key) == 1
    with Session(database) as session:
        steps = {
            step.step_name: step for step in get_workflow_run(session, run_id).steps
        }
        assert steps["c"].status == StepStatus.READY
        assert dequeue_step(client, queue_name=key) == steps["c"].id


def test_completion_and_readiness_roll_back_together(database, queue):
    client, key = queue
    workflow = Workflow("rollback_completion")
    workflow.step("a", lambda ctx: "ok")
    workflow.step("b", Mock(), depends_on=["a"])
    run_id = relay.run(workflow, {}, engine=database, redis=client, queue_name=key)
    with (
        patch(
            "app.services.execution.unlock_dependents",
            side_effect=RuntimeError("failure"),
        ),
        pytest.raises(RuntimeError, match="failure"),
    ):
        run_once(database, client, {workflow.name: workflow}, queue_name=key)
    with Session(database) as session:
        steps = {
            step.step_name: step for step in get_workflow_run(session, run_id).steps
        }
        assert steps["a"].status == StepStatus.RUNNING
        assert steps["a"].output is None and steps["a"].completed_at is None
        assert steps["b"].status == StepStatus.PENDING
    assert client.llen(key) == 0


def test_dispatch_failure_preserves_completion_and_ready_steps(database, queue):
    client, key = queue
    workflow = Workflow("redis_failure")
    first = Mock(return_value="done")
    workflow.step("a", first)
    workflow.step("b", lambda ctx: ctx["step_outputs"]["a"], depends_on=["a"])
    run_id = relay.run(workflow, {}, engine=database, redis=client, queue_name=key)
    registry = {workflow.name: workflow}
    with (
        patch.object(client, "rpush", side_effect=RedisConnectionError("offline")),
        pytest.raises(relay.QueueDispatchError) as error,
    ):
        run_once(database, client, registry, queue_name=key)
    assert error.value.run_id == run_id
    with Session(database) as session:
        steps = {
            step.step_name: step for step in get_workflow_run(session, run_id).steps
        }
        assert steps["a"].status == StepStatus.COMPLETED
        assert steps["a"].output == "done"
        assert steps["b"].status == StepStatus.READY
        assert error.value.step_run_ids == (steps["b"].id,)
    enqueue_steps(client, error.value.step_run_ids, queue_name=key)
    assert run_once(database, client, registry, queue_name=key)
    first.assert_called_once()
