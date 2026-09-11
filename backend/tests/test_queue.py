import json
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from app import relay
from app.core.config import Settings
from app.db.connections import create_redis_client
from app.models import StepStatus, WorkflowRun
from app.services.queue import dequeue_step, enqueue_step, enqueue_steps
from app.services.runs import get_workflow_run
from app.workflows import Workflow
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select
from sqlalchemy.orm import Session


@pytest.fixture
def queue():
    client = create_redis_client(Settings())
    key = f"relay:test:jobs:{uuid4().hex}"
    try:
        yield client, key
    finally:
        try:
            client.delete(key)
        finally:
            client.close()


@pytest.mark.integration
def test_fifo_identifiers_only_and_empty_queue(queue):
    client, key = queue
    first, second, third = uuid4(), uuid4(), uuid4()
    assert dequeue_step(client, queue_name=key) is None
    enqueue_step(client, first, queue_name=key)
    enqueue_steps(client, [second, third], queue_name=key)
    assert [json.loads(value) for value in client.lrange(key, 0, -1)] == [
        {"step_run_id": str(identifier)} for identifier in [first, second, third]
    ]
    assert [dequeue_step(client, queue_name=key) for _ in range(3)] == [
        first,
        second,
        third,
    ]
    assert dequeue_step(client, queue_name=key) is None


def test_batch_validated_before_writing_and_empty_batch_is_noop():
    client = Mock()
    enqueue_steps(client, [])
    with pytest.raises(ValueError):
        enqueue_steps(client, [uuid4(), "invalid"])
    client.rpush.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "message",
    [
        "not json",
        "[]",
        "{}",
        '{"step_run_id": 42}',
        '{"step_run_id": "bad"}',
        json.dumps({"step_run_id": str(uuid4()), "input": {}}),
    ],
)
def test_malformed_message_is_rejected_and_removed(queue, message):
    client, key = queue
    next_id = uuid4()
    client.rpush(key, message)
    enqueue_step(client, next_id, queue_name=key)
    with pytest.raises(ValueError, match="Invalid step queue message"):
        dequeue_step(client, queue_name=key)
    assert dequeue_step(client, queue_name=key) == next_id


@pytest.mark.integration
def test_creation_enqueues_only_roots_after_commit(database, queue):
    client, key = queue
    workflow = Workflow("queue_creation")
    handler = Mock(side_effect=AssertionError("Must not execute"))
    workflow.step("report", handler, depends_on=["a", "b"])
    workflow.step("a", handler)
    workflow.step("b", handler)
    real_rpush = client.rpush

    def verify_committed(queue_key, *messages):
        # A separate transaction must see the graph before Redis receives IDs.
        ids = [json.loads(message)["step_run_id"] for message in messages]
        with Session(database) as session:
            from uuid import UUID

            from app.models import StepRun

            steps = [session.get(StepRun, UUID(identifier)) for identifier in ids]
            assert {step.step_name for step in steps} == {"a", "b"}
            stored = get_workflow_run(session, steps[0].workflow_run_id)
            assert len(stored.steps) == 3 and len(stored.dependencies) == 2
            assert all(step.status == StepStatus.READY for step in steps)
        return real_rpush(queue_key, *messages)

    with patch.object(client, "rpush", side_effect=verify_committed) as push:
        run_id = relay.run(
            workflow,
            {"private_input": "stays in PostgreSQL"},
            engine=database,
            redis=client,
            queue_name=key,
        )
        push.assert_called_once()
    handler.assert_not_called()
    with Session(database) as session:
        stored = get_workflow_run(session, run_id)
        expected = {step.id for step in stored.steps if step.status == StepStatus.READY}
    assert {dequeue_step(client, queue_name=key) for _ in range(2)} == expected
    assert dequeue_step(client, queue_name=key) is None


@pytest.mark.integration
def test_redis_failure_reports_saved_run_and_allows_dispatch_retry(database, queue):
    client, key = queue
    workflow = Workflow("dispatch_retry")
    workflow.step("first", Mock())
    workflow.step("second", Mock())
    with Session(database) as session:
        before = session.scalar(select(func.count()).select_from(WorkflowRun))
    with (
        patch.object(client, "rpush", side_effect=RedisConnectionError("offline")),
        pytest.raises(relay.QueueDispatchError) as error,
    ):
        relay.run(workflow, {}, engine=database, redis=client, queue_name=key)
    saved_id = error.value.run_id
    with Session(database) as session:
        stored = get_workflow_run(session, saved_id)
        assert {step.id for step in stored.steps} == set(error.value.step_run_ids)
        assert all(step.status == StepStatus.READY for step in stored.steps)
        assert (
            session.scalar(select(func.count()).select_from(WorkflowRun)) == before + 1
        )
    enqueue_steps(client, error.value.step_run_ids, queue_name=key)
    assert {dequeue_step(client, queue_name=key) for _ in range(2)} == set(
        error.value.step_run_ids
    )


@pytest.mark.integration
def test_database_failure_does_not_enqueue(database, queue):
    client, key = queue
    workflow = Workflow("db_failure")
    workflow.step("first", Mock())
    with (
        patch(
            "app.services.workflows.create_step_dependencies",
            side_effect=RuntimeError("failed"),
        ),
        pytest.raises(RuntimeError, match="failed"),
    ):
        relay.run(workflow, {}, engine=database, redis=client, queue_name=key)
    assert client.llen(key) == 0


@pytest.mark.integration
def test_default_redis_client_is_closed_and_supplied_client_is_not(database, queue):
    client, key = queue
    workflow = Workflow("client_lifecycle")
    workflow.step("work", Mock())
    with patch.object(client, "close", wraps=client.close) as close:
        relay.run(workflow, {}, engine=database, redis=client, queue_name=key)
        close.assert_not_called()
        with patch("app.services.workflows.create_redis_client", return_value=client):
            relay.run(workflow, {}, engine=database, queue_name=key)
        close.assert_called_once()


@pytest.mark.integration
def test_invalid_redis_url_reports_the_saved_run(database, monkeypatch):
    monkeypatch.setenv("REDIS_URL", "invalid-url")
    workflow = Workflow("invalid_queue_config")
    workflow.step("work", Mock())
    with pytest.raises(relay.QueueDispatchError) as error:
        relay.run(workflow, {}, engine=database)
    with Session(database) as session:
        stored = get_workflow_run(session, error.value.run_id)
        assert stored.steps[0].id == error.value.step_run_ids[0]
        assert stored.steps[0].status == StepStatus.READY
