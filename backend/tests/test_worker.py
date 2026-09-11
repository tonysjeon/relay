from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app import relay
from app.core.config import Settings
from app.db.connections import create_redis_client
from app.models import StepRun, StepStatus, WorkflowRun, WorkflowStatus
from app.services.execution import execute_step
from app.services.queue import enqueue_step
from app.services.runs import get_workflow_run
from app.workers.worker import run_once
from app.workflows import Workflow
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def queue():
    client = create_redis_client(Settings())
    key = f"relay:test:worker:{uuid4().hex}"
    try:
        yield client, key
    finally:
        client.delete(key)
        client.close()


def create_run(database, queue, handler, *, downstream=False):
    client, key = queue
    workflow = Workflow("worker_test")
    workflow.step("fetch", handler)
    if downstream:
        workflow.step("report", lambda ctx: ctx, depends_on=["fetch"])
    run_id = relay.run(
        workflow, {"company": "Stripe"}, engine=database, redis=client, queue_name=key
    )
    with Session(database) as session:
        run = get_workflow_run(session, run_id)
        step_ids = {step.step_name: step.id for step in run.steps}
    return workflow, run_id, step_ids


def test_worker_executes_queued_root_and_persists_output(database, queue):
    handler = Mock(return_value={"company": "Stripe", "score": 92})
    workflow, run_id, ids = create_run(database, queue, handler, downstream=True)
    client, key = queue
    assert run_once(database, client, {workflow.name: workflow}, queue_name=key)
    handler.assert_called_once_with(
        {"workflow_input": {"company": "Stripe"}, "step_outputs": {}}
    )
    with Session(database) as session:
        run = get_workflow_run(session, run_id)
        steps = {step.step_name: step for step in run.steps}
        assert run.status == WorkflowStatus.RUNNING and run.started_at is not None
        assert steps["fetch"].status == StepStatus.COMPLETED
        assert steps["fetch"].output == {"company": "Stripe", "score": 92}
        assert steps["fetch"].attempt_count == 1
        assert steps["fetch"].input == {
            "workflow_input": {"company": "Stripe"},
            "step_outputs": {},
        }
        assert steps["fetch"].started_at <= steps["fetch"].completed_at
        assert steps["report"].status == StepStatus.PENDING
        assert steps["report"].attempt_count == 0
    assert client.llen(key) == 0
    enqueue_step(client, ids["fetch"], queue_name=key)
    assert not run_once(database, client, {workflow.name: workflow}, queue_name=key)
    assert handler.call_count == 1


def test_claim_is_committed_before_handler_and_connection_is_released(database, queue):
    ids = {}

    def handler(ctx):
        with Session(database) as session:
            step = session.get(StepRun, ids["fetch"])
            assert step.status == StepStatus.RUNNING and step.attempt_count == 1
        return "saved"

    workflow, _, ids = create_run(database, queue, handler)
    assert execute_step(database, ids["fetch"], {workflow.name: workflow})


def test_worker_builds_context_from_persisted_dependencies(database, queue):
    workflow, _, ids = create_run(
        database, queue, lambda ctx: {"source": ctx["workflow_input"]}, downstream=True
    )
    registry = {workflow.name: workflow}
    assert execute_step(database, ids["fetch"], registry)
    with Session(database) as session, session.begin():
        session.get(StepRun, ids["report"]).status = StepStatus.READY
    assert execute_step(database, ids["report"], registry)
    with Session(database) as session:
        step = session.get(StepRun, ids["report"])
        assert step.output == {
            "workflow_input": {"company": "Stripe"},
            "step_outputs": {"fetch": {"source": {"company": "Stripe"}}},
        }


def test_duplicate_claims_execute_once(database, queue):
    handler = Mock(return_value={"ok": True})
    workflow, _, ids = create_run(database, queue, handler)
    barrier = Barrier(2)

    def execute():
        barrier.wait(timeout=10)
        return execute_step(database, ids["fetch"], {workflow.name: workflow})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute) for _ in range(2)]
        assert sorted(future.result(timeout=15) for future in futures) == [False, True]
    handler.assert_called_once()


def test_empty_queue_unknown_id_and_pending_step_are_skipped(database, queue):
    client, key = queue
    assert not run_once(database, client, {}, queue_name=key)
    assert not execute_step(database, uuid4(), {})
    handler = Mock()
    workflow, _, ids = create_run(database, queue, handler, downstream=True)
    assert not execute_step(database, ids["report"], {workflow.name: workflow})
    handler.assert_not_called()


def test_missing_handler_does_not_claim_step(database, queue):
    _, _, ids = create_run(database, queue, Mock())
    with pytest.raises(LookupError, match="No registered handler"):
        execute_step(database, ids["fetch"], {})
    with Session(database) as session:
        assert session.get(StepRun, ids["fetch"]).status == StepStatus.READY


def test_incomplete_dependencies_cannot_execute(database, queue):
    workflow, _, ids = create_run(database, queue, Mock(), downstream=True)
    with Session(database) as session, session.begin():
        session.get(StepRun, ids["report"]).status = StepStatus.READY
    with pytest.raises(ValueError, match="incomplete dependencies"):
        execute_step(database, ids["report"], {workflow.name: workflow})


@pytest.mark.parametrize("output", [None, [1, True, "value"], "text", 42])
def test_json_output_types(database, queue, output):
    workflow, _, ids = create_run(database, queue, Mock(return_value=output))
    assert execute_step(database, ids["fetch"], {workflow.name: workflow})
    with Session(database) as session:
        assert session.get(StepRun, ids["fetch"]).output == output


@pytest.mark.parametrize(
    "handler,exception",
    [
        (Mock(side_effect=RuntimeError("handler failed")), RuntimeError),
        (Mock(return_value=object()), TypeError),
        (Mock(return_value=float("nan")), ValueError),
    ],
)
def test_failures_propagate_without_marking_step_completed(
    database, queue, handler, exception
):
    workflow, _, ids = create_run(database, queue, handler)
    with pytest.raises(exception):
        execute_step(database, ids["fetch"], {workflow.name: workflow})
    with Session(database) as session:
        step = session.get(StepRun, ids["fetch"])
        assert step.status == StepStatus.RUNNING
        assert step.output is None and step.completed_at is None


@pytest.mark.parametrize(
    "status",
    [WorkflowStatus.CANCELLED, WorkflowStatus.COMPLETED, WorkflowStatus.FAILED],
)
def test_terminal_workflow_is_not_executed(database, queue, status):
    handler = Mock()
    workflow, run_id, ids = create_run(database, queue, handler)
    with Session(database) as session, session.begin():
        session.get(WorkflowRun, run_id).status = status
    assert not execute_step(database, ids["fetch"], {workflow.name: workflow})
    handler.assert_not_called()
