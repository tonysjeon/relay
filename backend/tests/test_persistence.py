from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.models import (
    StepDependency,
    StepRun,
    StepStatus,
    Worker,
    WorkflowRun,
    WorkflowStatus,
)
from app.services.runs import (
    create_step_dependencies,
    create_step_runs,
    create_workflow_run,
    get_step_runs,
    get_workflow_run,
    update_step_status,
)
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def migrate(engine, operation, revision=None):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        if revision is None:
            operation(config)
        else:
            operation(config, revision)


@pytest.fixture
def session(database):
    with database.connect() as connection:
        transaction = connection.begin()
        with Session(connection) as session:
            yield session
        if transaction.is_active:
            transaction.rollback()


def test_reconstruct_committed_graph_in_new_session(database):
    with Session(database) as session, session.begin():
        run = create_workflow_run(session, "research", {"company": "Stripe"})
        run_id = run.id
        steps = create_step_runs(session, run.id, ["fetch", "analyze", "report"])
        create_step_dependencies(
            session, run.id, {"analyze": ["fetch"], "report": ["analyze"]}
        )
        steps[0].output = {"items": [1, 2], "nested": {"ok": True}}
        update_step_status(session, steps[0].id, StepStatus.COMPLETED)

    try:
        with Session(database) as session:
            run = get_workflow_run(session, run_id)
            assert run.workflow_name == "research"
            assert run.input == {"company": "Stripe"}
            assert run.status == WorkflowStatus.PENDING
            assert run.created_at.tzinfo is not None
            assert run.started_at is None and run.completed_at is None
            names = {step.id: step.step_name for step in run.steps}
            assert set(names.values()) == {"fetch", "analyze", "report"}
            assert {
                (names[edge.step_run_id], names[edge.depends_on_step_run_id])
                for edge in run.dependencies
            } == {("analyze", "fetch"), ("report", "analyze")}
            steps = {step.step_name: step for step in get_step_runs(session, run_id)}
            assert steps["fetch"].status == StepStatus.COMPLETED
            assert steps["fetch"].output == {"items": [1, 2], "nested": {"ok": True}}
            assert steps["analyze"].status == StepStatus.PENDING
            assert steps["analyze"].attempt_count == 0
            assert steps["analyze"].max_attempts == 1
            assert steps["analyze"].lease_owner is None
            assert steps["analyze"].output is None
    finally:
        with Session(database) as session, session.begin():
            session.delete(session.get(WorkflowRun, run_id))


def test_worker_and_step_execution_fields_round_trip(session):
    now = datetime.now(timezone.utc)
    run = create_workflow_run(session, "fields", {})
    step = create_step_runs(session, run.id, ["work"], max_attempts=3)[0]
    step_id = step.id
    step.input = {"payload": "hello"}
    step.output = [1, "result", False]
    step.error = "temporary failure"
    step.attempt_count = 1
    step.next_retry_at = now
    step.lease_owner = "worker-one"
    step.lease_expires_at = now
    step.started_at = now
    step.completed_at = now
    run.started_at = now
    run.completed_at = now
    run.status = WorkflowStatus.FAILED
    session.add(Worker(id="worker-one", last_heartbeat=now))
    session.flush()
    session.expire_all()
    stored = session.get(StepRun, step_id)
    assert stored.input == {"payload": "hello"}
    assert stored.output == [1, "result", False]
    assert stored.error == "temporary failure"
    assert stored.attempt_count == 1 and stored.max_attempts == 3
    assert stored.lease_owner == "worker-one"
    for field in ("next_retry_at", "lease_expires_at", "started_at", "completed_at"):
        assert getattr(stored, field) == now
    worker = session.get(Worker, "worker-one")
    assert worker.last_heartbeat == now
    assert worker.started_at.tzinfo is not None
    assert run.status == WorkflowStatus.FAILED
    assert run.started_at == now and run.completed_at == now


@pytest.mark.parametrize("status", list(StepStatus))
def test_update_step_status(session, status):
    run = create_workflow_run(session, "status", {})
    step = create_step_runs(session, run.id, ["work"])[0]
    step_id = step.id
    update_step_status(session, step_id, status)
    session.expire_all()
    assert session.get(StepRun, step_id).status == status


def test_unknown_runs_and_steps(session):
    assert get_workflow_run(session, uuid4()) is None
    assert get_step_runs(session, uuid4()) == []
    with pytest.raises(LookupError):
        update_step_status(session, uuid4(), StepStatus.RUNNING)


def test_transaction_rollback_removes_partial_workflow(database):
    with Session(database) as session:
        run_id = create_workflow_run(session, "rollback", {}).id
        create_step_runs(session, run_id, ["first", "second"])
        session.rollback()
    with Session(database) as session:
        assert get_workflow_run(session, run_id) is None
        assert get_step_runs(session, run_id) == []


@pytest.mark.parametrize(
    "invalid", ["duplicate_name", "missing_workflow", "max_attempts", "attempt_count"]
)
def test_invalid_steps_rejected_by_database(session, invalid):
    run = create_workflow_run(session, "constraints", {})
    create_step_runs(session, run.id, ["work"])
    with pytest.raises(IntegrityError), session.begin_nested():
        if invalid == "duplicate_name":
            create_step_runs(session, run.id, ["work"])
        elif invalid == "missing_workflow":
            create_step_runs(session, uuid4(), ["work"])
        elif invalid == "max_attempts":
            create_step_runs(session, run.id, ["other"], max_attempts=0)
        else:
            session.add(
                StepRun(workflow_run_id=run.id, step_name="other", attempt_count=-1)
            )
            session.flush()


@pytest.mark.parametrize("invalid", ["cross_run", "self", "duplicate", "missing_step"])
def test_invalid_dependencies_rejected_by_database(session, invalid):
    run = create_workflow_run(session, "first", {})
    a, b = create_step_runs(session, run.id, ["a", "b"])
    other = create_workflow_run(session, "second", {})
    foreign = create_step_runs(session, other.id, ["foreign"])[0]
    create_step_dependencies(session, run.id, {"b": ["a"]})
    parent_id = {
        "cross_run": foreign.id,
        "self": b.id,
        "duplicate": a.id,
        "missing_step": uuid4(),
    }[invalid]
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            StepDependency(
                workflow_run_id=run.id,
                step_run_id=b.id,
                depends_on_step_run_id=parent_id,
            )
        )
        session.flush()


def test_missing_dependency_name_rejected_before_insert(session):
    run = create_workflow_run(session, "missing", {})
    create_step_runs(session, run.id, ["a", "b"])
    with pytest.raises(ValueError):
        create_step_dependencies(session, run.id, {"b": ["a"], "unknown": ["a"]})
    assert session.scalars(select(StepDependency)).all() == []


def test_deleting_workflow_cascades_to_steps_and_dependencies(session):
    run = create_workflow_run(session, "delete", {})
    create_step_runs(session, run.id, ["a", "b"])
    create_step_dependencies(session, run.id, {"b": ["a"]})
    session.delete(run)
    session.flush()
    assert session.scalars(select(StepRun)).all() == []
    assert session.scalars(select(StepDependency)).all() == []


def test_migration_round_trip_and_model_parity(database):
    migrate(database, command.check)
    migrate(database, command.downgrade, "base")
    assert set(inspect(database).get_table_names()) == {"alembic_version"}
    with database.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN ('step_status', 'workflow_status')"
                )
            ).all()
            == []
        )
    migrate(database, command.upgrade, "head")
    migrate(database, command.upgrade, "head")
    migrate(database, command.check)
    assert set(inspect(database).get_table_names()) == {
        "alembic_version",
        "workflow_runs",
        "step_runs",
        "step_attempts",
        "llm_calls",
        "coding_events",
        "step_dependencies",
        "workers",
    }
