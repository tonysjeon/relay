from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from app import relay
from app.main import app
from app.models import StepStatus, WorkflowStatus
from app.services.approvals import decide_approval
from app.services.execution import execute_step
from app.services.recovery import recover_abandoned_steps
from app.services.workflow_api import WorkflowConflict, cancel_workflow, workflow_detail
from app.workflows import Workflow
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError

pytestmark = pytest.mark.integration


def create(database):
    workflow = Workflow("approval_test")
    workflow.step(
        "draft", lambda ctx: {"draft": "Review this"}, requires_approval=True, retries=2
    )
    workflow.step(
        "publish", lambda ctx: ctx["step_outputs"]["draft"], depends_on=["draft"]
    )
    run_id = relay.run(
        workflow, {}, engine=database, redis=Mock(), queue_name="relay:approval-test"
    )
    steps = {s.step_name: s.id for s in workflow_detail(database, run_id).steps}
    registry = {workflow.name: workflow}
    assert execute_step(database, steps["draft"], registry, redis=Mock())
    return run_id, steps, registry


def test_waiting_releases_lease_and_approval_unlocks_once(database):
    run_id, steps, registry = create(database)
    run = workflow_detail(database, run_id)
    draft = next(s for s in run.steps if s.step_name == "draft")
    assert draft.status == StepStatus.WAITING_APPROVAL
    assert draft.output == {"draft": "Review this"}
    assert draft.lease_owner is None and draft.lease_expires_at is None
    assert draft.completed_at is None and draft.approval_requested_at
    assert draft.attempts[0].status == "COMPLETED"
    assert run.status == WorkflowStatus.RUNNING
    assert not execute_step(database, steps["draft"], registry, redis=Mock())
    assert not execute_step(database, steps["publish"], registry, redis=Mock())
    recover_abandoned_steps(database, redis=Mock())
    assert (
        workflow_detail(database, run_id).steps[0].status == StepStatus.WAITING_APPROVAL
    )
    redis = Mock()
    decide_approval(
        database, run_id, steps["draft"], "approved", "Looks good", redis=redis
    )
    decide_approval(
        database, run_id, steps["draft"], "approved", "Looks good", redis=redis
    )
    assert redis.rpush.call_count == 1
    assert redis.rpush.call_args.args[0] == "relay:approval-test"
    assert execute_step(database, steps["publish"], registry, redis=Mock())
    run = workflow_detail(database, run_id)
    assert run.status == WorkflowStatus.COMPLETED
    assert run.steps[0].approval_note == "Looks good"
    assert run.steps[0].approval_decided_at
    assert run.steps[0].attempt_count == 1


def test_rejection_and_cancellation_prevent_continuation(database):
    run_id, steps, registry = create(database)
    decide_approval(
        database, run_id, steps["draft"], "rejected", "Revise", redis=Mock()
    )
    assert workflow_detail(database, run_id).status == WorkflowStatus.FAILED
    assert not execute_step(database, steps["publish"], registry, redis=Mock())
    with pytest.raises(WorkflowConflict):
        decide_approval(database, run_id, steps["draft"], "approved", redis=Mock())
    run_id, steps, registry = create(database)
    cancel_workflow(database, run_id)
    with pytest.raises(WorkflowConflict):
        decide_approval(database, run_id, steps["draft"], "approved", redis=Mock())


def test_concurrent_opposing_decisions_only_one_wins(database):
    run_id, steps, _ = create(database)

    def decide(value):
        try:
            decide_approval(database, run_id, steps["draft"], value, redis=Mock())
            return value
        except WorkflowConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, ["approved", "rejected"]))
    assert sum(value is not None for value in results) == 1
    assert workflow_detail(database, run_id).steps[0].approval_decision in results


def test_dispatch_failure_keeps_approved_ready_work(database):
    run_id, steps, _ = create(database)
    redis = Mock()
    redis.rpush.side_effect = ConnectionError("offline")
    decide_approval(database, run_id, steps["draft"], "approved", redis=redis)
    run = workflow_detail(database, run_id)
    assert run.steps[0].approval_decision == "approved"
    assert (
        next(s for s in run.steps if s.step_name == "publish").status
        == StepStatus.READY
    )


def test_approval_api_validation_and_conflicts(database):
    run_id, steps, _ = create(database)
    with (
        patch("app.main.create_database_engine", return_value=database),
        patch("app.main.create_redis_client", return_value=Mock()),
        patch("app.services.approvals.create_redis_client", return_value=Mock()),
        TestClient(app) as client,
    ):
        url = f"/workflows/{run_id}/steps/{steps['draft']}/approval"
        assert client.post(url, json={"decision": "invalid"}).status_code == 422
        assert (
            client.post(
                url, json={"decision": "approved", "note": "x" * 2001}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/workflows/{run_id}/steps/{uuid4()}/approval",
                json={"decision": "approved"},
            ).status_code
            == 404
        )
        response = client.post(url, json={"decision": "approved", "note": "Reviewed"})
        assert response.status_code == 200
        assert response.json()["steps"][0]["approval_decision"] == "approved"
        assert client.post(url, json={"decision": "rejected"}).status_code == 409
