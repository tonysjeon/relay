from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from app import relay
from app.models import LLMCall, StepRun
from app.services.execution import execute_step
from app.services.recovery import recover_abandoned_steps
from app.services.retries import schedule_retries
from app.services.workflow_api import workflow_detail
from app.workflows import Workflow
from sqlalchemy import select
from sqlalchemy.orm import Session


def make_run(database, handler, retries=1):
    workflow = Workflow("tracked_workflow")
    workflow.step("work", handler, retries=retries)
    run_id = relay.run(workflow, {}, engine=database, redis=Mock())
    step_id = workflow_detail(database, run_id).steps[0].id
    return workflow, run_id, step_id


def execute(database, workflow, step_id):
    return execute_step(database, step_id, {workflow.name: workflow}, redis=Mock())


def test_outside_handler_rejected():
    with (
        pytest.raises(RuntimeError, match="inside a Relay"),
        relay.llm_call(provider="test", model="test", input="prompt"),
    ):
        pass


@pytest.mark.integration
def test_multiple_calls_visible_while_running_and_saved_after_completion(database):
    run_id = None

    def handler(ctx):
        with relay.llm_call(
            provider="test", model="model-a", input={"prompt": "hello"}
        ) as call:
            live = workflow_detail(database, run_id).steps[0].attempts[0].llm_calls
            assert live[0].status == "RUNNING"
            call.set_result({"text": "world"}, input_tokens=4, output_tokens=2)
        with relay.llm_call(provider="other", model="model-b", input="next") as call:
            call.set_result(None)
        return "done"

    workflow, run_id, step_id = make_run(database, handler)
    assert execute(database, workflow, step_id)
    calls = workflow_detail(database, run_id).steps[0].attempts[0].llm_calls
    assert len(calls) == 2
    assert [c.status for c in calls] == ["COMPLETED", "COMPLETED"]
    assert calls[0].input == {"prompt": "hello"}
    assert calls[0].output == {"text": "world"}
    assert calls[0].input_tokens == 4 and calls[0].output_tokens == 2
    assert calls[1].input_tokens is None and calls[1].output is None
    assert all(c.completed_at >= c.started_at for c in calls)
    assert not execute(database, workflow, step_id)
    with (
        pytest.raises(RuntimeError),
        relay.llm_call(provider="test", model="test", input="outside"),
    ):
        pass
    with Session(database) as session, session.begin():
        session.delete(session.get(StepRun, step_id))
    with Session(database) as session:
        assert session.scalar(select(LLMCall).where(LLMCall.id == calls[0].id)) is None


@pytest.mark.integration
def test_retry_retains_failed_call_and_adds_new_call(database):
    count = 0

    def handler(ctx):
        nonlocal count
        count += 1
        with relay.llm_call(provider="test", model="test", input="prompt") as call:
            if count == 1:
                raise ValueError("provider failure")
            call.set_result("answer", input_tokens=0, output_tokens=1)
        return "done"

    workflow, run_id, step_id = make_run(database, handler)
    assert execute(database, workflow, step_id)
    schedule_retries(
        database, Mock(), now=datetime.now(timezone.utc) + timedelta(seconds=10)
    )
    assert execute(database, workflow, step_id)
    attempts = workflow_detail(database, run_id).steps[0].attempts
    assert attempts[0].llm_calls[0].status == "FAILED"
    assert attempts[0].llm_calls[0].error == "ValueError"
    assert attempts[1].llm_calls[0].status == "COMPLETED"
    assert attempts[0].llm_calls[0].id != attempts[1].llm_calls[0].id


@pytest.mark.integration
def test_lost_lease_abandons_call_and_rejects_stale_result(database):
    step_id = None

    def handler(ctx):
        with relay.llm_call(provider="test", model="test", input="prompt") as call:
            with Session(database) as session, session.begin():
                session.get(StepRun, step_id).lease_expires_at = datetime.now(
                    timezone.utc
                ) - timedelta(seconds=1)
            recover_abandoned_steps(database, Mock())
            call.set_result("stale")
        return "stale"

    workflow, run_id, step_id = make_run(database, handler)
    assert not execute(database, workflow, step_id)
    call = workflow_detail(database, run_id).steps[0].attempts[0].llm_calls[0]
    assert call.status == "ABANDONED"
    assert call.output is None
    assert call.completed_at is not None


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["missing", "tokens", "json"])
def test_invalid_results_fail_call_and_step(database, mode):
    def handler(ctx):
        with relay.llm_call(provider="test", model="test", input="prompt") as call:
            if mode == "tokens":
                call.set_result("answer", input_tokens=-1)
            elif mode == "json":
                call.set_result(float("nan"))
        return "done"

    workflow, run_id, step_id = make_run(database, handler, retries=0)
    assert execute(database, workflow, step_id)
    detail = workflow_detail(database, run_id)
    assert detail.status == "FAILED"
    assert detail.steps[0].attempts[0].llm_calls[0].status == "FAILED"


@pytest.mark.integration
def test_parallel_handlers_keep_call_context_separate(database):
    def handler(ctx):
        with relay.llm_call(
            provider="test", model="test", input=ctx["idempotency_key"]
        ) as call:
            call.set_result(ctx["idempotency_key"])
        return "done"

    first = make_run(database, handler)
    second = make_run(database, handler)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(
            pool.map(lambda args: execute(database, args[0], args[2]), [first, second])
        )
    for _, run_id, step_id in (first, second):
        call = workflow_detail(database, run_id).steps[0].attempts[0].llm_calls[0]
        assert call.input == call.output == f"{run_id}:work"
