from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from app import relay
from app.models import StepAttempt, StepRun, StepStatus
from app.services.execution import execute_step
from app.services.failures import record_failure
from app.services.recovery import recover_abandoned_steps
from app.services.retries import schedule_retries
from app.services.runs import get_workflow_run
from app.services.workflow_api import cancel_workflow, workflow_detail
from app.workflows import Workflow
from sqlalchemy import func, select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def create(database, handler, retries=1):
    workflow = Workflow("attempt_history")
    workflow.step("work", handler, retries=retries)
    run_id = relay.run(workflow, {}, engine=database, redis=Mock())
    with Session(database) as session:
        step_id = get_workflow_run(session, run_id).steps[0].id
    return workflow, run_id, step_id


def test_retry_keeps_failure_worker_and_timing_after_success(database):
    handler = Mock(side_effect=[ValueError("first failed"), {"ok": True}])
    workflow, run_id, step_id = create(database, handler)
    registry = {workflow.name: workflow}
    assert execute_step(database, step_id, registry, worker_id="first")
    assert (
        schedule_retries(
            database, Mock(), now=datetime.now(timezone.utc) + timedelta(seconds=10)
        )
        >= 1
    )
    assert execute_step(database, step_id, registry, worker_id="second")
    assert not execute_step(database, step_id, registry, worker_id="duplicate")
    step = workflow_detail(database, run_id).steps[0]
    assert step.error is None
    assert [a.status for a in step.attempts] == ["FAILED", "COMPLETED"]
    assert [a.worker_id for a in step.attempts] == ["first", "second"]
    assert [a.attempt_number for a in step.attempts] == [1, 2]
    assert step.attempts[0].error == "ValueError: first failed"
    assert step.attempts[1].error is None
    assert all(a.completed_at >= a.started_at for a in step.attempts)
    with Session(database) as session, session.begin():
        session.delete(session.get(StepRun, step_id))
    with Session(database) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(StepAttempt)
                .where(StepAttempt.step_run_id == step_id)
            )
            == 0
        )


@pytest.mark.parametrize("fails", [False, True])
def test_recovery_preserves_abandonment_against_stale_worker(database, fails):
    step_id = None

    def abandoned(ctx):
        # Simulate losing the lease while user code is still executing.
        with Session(database) as session, session.begin():
            session.get(StepRun, step_id).lease_expires_at = datetime.now(
                timezone.utc
            ) - timedelta(seconds=1)
        assert recover_abandoned_steps(database, Mock()) >= 1
        if fails:
            raise ValueError("stale error")
        return "stale output"

    workflow, run_id, step_id = create(database, abandoned)
    assert not execute_step(
        database, step_id, {workflow.name: workflow}, worker_id="lost"
    )
    replacement = Workflow(workflow.name)
    replacement.step("work", lambda ctx: "fresh")
    assert execute_step(
        database, step_id, {workflow.name: replacement}, worker_id="replacement"
    )
    assert (
        record_failure(
            database, run_id, step_id, ValueError("late"), worker_id="lost", attempt=1
        )
        is None
    )
    attempts = workflow_detail(database, run_id).steps[0].attempts
    assert [a.status for a in attempts] == ["ABANDONED", "COMPLETED"]
    assert attempts[0].error == "Worker lease expired"
    assert attempts[1].worker_id == "replacement"


def test_running_attempt_visible_and_cancellation_keeps_actual_outcome(database):
    run_id = None

    def handler(ctx):
        attempt = workflow_detail(database, run_id).steps[0].attempts[0]
        assert attempt.status == "RUNNING"
        assert attempt.completed_at is None
        cancel_workflow(database, run_id)
        return "finished after cancellation"

    workflow, run_id, step_id = create(database, handler)
    assert execute_step(
        database, step_id, {workflow.name: workflow}, worker_id="active"
    )
    detail = workflow_detail(database, run_id)
    assert detail.status == "CANCELLED"
    assert detail.steps[0].attempts[0].status == "COMPLETED"


def test_old_steps_have_no_fabricated_history(database):
    _, run_id, step_id = create(database, Mock())
    with Session(database) as session, session.begin():
        step = session.get(StepRun, step_id)
        step.status = StepStatus.COMPLETED
        step.attempt_count = 1
    assert workflow_detail(database, run_id).steps[0].attempts == []


def test_concurrent_claims_create_one_attempt(database):
    workflow, run_id, step_id = create(database, lambda ctx: "done")

    def claim(owner):
        return execute_step(
            database, step_id, {workflow.name: workflow}, worker_id=owner
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["worker-a", "worker-b"]))
    assert sorted(results) == [False, True]
    attempts = workflow_detail(database, run_id).steps[0].attempts
    assert len(attempts) == 1
    assert attempts[0].status == "COMPLETED"
