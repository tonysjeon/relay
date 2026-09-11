import time
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from app import relay
from app.models import StepRun, StepStatus
from app.services.execution import execute_step
from app.services.failures import record_failure
from app.services.leases import renew_lease
from app.services.runs import get_workflow_run
from app.workflows import Workflow
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def make_step(database, handler):
    workflow = Workflow("lease_test")
    workflow.step("work", handler, retries=1)
    run_id = relay.run(workflow, {}, engine=database, redis=Mock())
    with Session(database) as session:
        step_id = get_workflow_run(session, run_id).steps[0].id
    return workflow, run_id, step_id


def test_lease_renews_through_long_handler_and_clears_on_completion(database):
    step_id = None

    def handler(ctx):
        with Session(database) as session:
            initial = session.get(StepRun, step_id)
            assert initial.lease_owner == "worker-test"
            expiry = initial.lease_expires_at
        time.sleep(1.5)
        with Session(database) as session:
            assert session.get(StepRun, step_id).lease_expires_at > expiry
        return "done"

    workflow, _, step_id = make_step(database, handler)
    assert execute_step(
        database,
        step_id,
        {workflow.name: workflow},
        worker_id="worker-test",
        lease_seconds=1,
    )
    with Session(database) as session:
        step = session.get(StepRun, step_id)
        assert step.status == StepStatus.COMPLETED and step.output == "done"
        assert step.lease_owner is None and step.lease_expires_at is None


@pytest.mark.parametrize("change", ["owner", "attempt", "expiry"])
@pytest.mark.parametrize("fails", [False, True])
def test_stale_attempt_cannot_save_output_or_failure(database, change, fails):
    step_id = None

    def handler(ctx):
        with Session(database) as session, session.begin():
            step = session.get(StepRun, step_id)
            if change == "owner":
                step.lease_owner = "replacement"
            elif change == "attempt":
                step.attempt_count += 1
            else:
                step.lease_expires_at = datetime.now(timezone.utc) - timedelta(
                    seconds=1
                )
        if fails:
            raise RuntimeError("stale failure")
        return "stale output"

    workflow, _, step_id = make_step(database, handler)
    assert not execute_step(
        database, step_id, {workflow.name: workflow}, worker_id="original"
    )
    with Session(database) as session:
        step = session.get(StepRun, step_id)
        assert step.status == StepStatus.RUNNING
        assert step.output is None and step.error is None and step.completed_at is None


def test_renewal_requires_current_owner_attempt_and_unexpired_lease(database):
    _, run_id, step_id = make_step(database, Mock())
    with Session(database) as session, session.begin():
        step = session.get(StepRun, step_id)
        step.status, step.attempt_count, step.lease_owner = (
            StepStatus.RUNNING,
            1,
            "original",
        )
        step.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=10)
    assert not renew_lease(database, step_id, "other", 1, 30)
    assert not renew_lease(database, step_id, "original", 2, 30)
    assert renew_lease(database, step_id, "original", 1, 30)
    with Session(database) as session, session.begin():
        session.get(StepRun, step_id).lease_expires_at = datetime.now(
            timezone.utc
        ) - timedelta(seconds=1)
    assert not renew_lease(database, step_id, "original", 1, 30)
    assert (
        record_failure(
            database,
            run_id,
            step_id,
            RuntimeError("late"),
            worker_id="original",
            attempt=1,
        )
        is None
    )


def test_handler_failure_releases_lease(database):
    workflow, _, step_id = make_step(database, Mock(side_effect=RuntimeError("retry")))
    assert execute_step(
        database, step_id, {workflow.name: workflow}, worker_id="worker-test"
    )
    with Session(database) as session:
        step = session.get(StepRun, step_id)
        assert step.status == StepStatus.RETRYING
        assert step.lease_owner is None and step.lease_expires_at is None
