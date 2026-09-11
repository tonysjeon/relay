from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from app import relay
from app.core.config import Settings
from app.db.connections import create_redis_client
from app.models import StepRun
from app.services.recovery import recover_abandoned_steps
from app.services.retries import schedule_retries
from app.services.runs import get_workflow_run
from app.workers.worker import run_once
from app.workflows import Workflow
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("failure", ["exception", "expired_lease"])
def test_keys_survive_retries_and_differ_between_steps_and_runs(database, failure):
    client = create_redis_client(Settings())
    queue = f"relay:test:idempotency:{uuid4().hex}"
    seen = []
    ids = {}

    def handler(ctx):
        seen.append(ctx["idempotency_key"])
        if len(seen) == 1:
            if failure == "exception":
                raise RuntimeError("retry me")
            with Session(database) as session, session.begin():
                session.get(StepRun, ids["a"]).lease_expires_at = datetime.now(
                    timezone.utc
                ) - timedelta(seconds=1)
        return ctx["idempotency_key"]

    workflow = Workflow("keys")
    workflow.step("a", handler, retries=1)
    workflow.step("b", lambda ctx: ctx["idempotency_key"], depends_on=["a"])
    registry = {workflow.name: workflow}
    try:
        run_id = relay.run(
            workflow, {}, engine=database, redis=client, queue_name=queue
        )
        with Session(database) as session:
            ids.update(
                {s.step_name: s.id for s in get_workflow_run(session, run_id).steps}
            )
        run_once(database, client, registry, queue_name=queue)
        if failure == "exception":
            with Session(database) as session:
                due = session.get(StepRun, ids["a"]).next_retry_at
            assert schedule_retries(database, client, queue_name=queue, now=due) == 1
        else:
            assert recover_abandoned_steps(database, client, queue_name=queue) == 1
        assert run_once(database, client, registry, queue_name=queue)
        assert run_once(database, client, registry, queue_name=queue)
        assert seen == [f"{run_id}:a", f"{run_id}:a"]
        with Session(database) as session:
            assert session.get(StepRun, ids["b"]).output == f"{run_id}:b"
            assert session.get(StepRun, ids["a"]).input["idempotency_key"] == seen[0]
        other = relay.run(workflow, {}, engine=database, redis=client, queue_name=queue)
        assert run_once(database, client, registry, queue_name=queue)
        assert seen[-1] == f"{other}:a" and seen[-1] != seen[0]
    finally:
        client.delete(queue)
        client.close()
