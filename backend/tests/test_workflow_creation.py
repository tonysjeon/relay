from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from app import relay
from app.models import StepDependency, StepRun, StepStatus, WorkflowRun, WorkflowStatus
from app.services.runs import get_workflow_run
from app.workflows import Workflow
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@pytest.mark.parametrize(
    "graph",
    [
        {},
        {"a": ["missing"]},
        {"a": ["b"], "b": ["a"]},
    ],
)
def test_invalid_graph_rejected_before_database_access(graph):
    workflow = Workflow("invalid")
    callback = Mock()
    for name, dependencies in graph.items():
        workflow.step(name, callback, depends_on=dependencies)
    with patch("app.services.workflows.create_database_engine") as create_engine:
        with pytest.raises(ValueError):
            relay.run(workflow, {})
        create_engine.assert_not_called()
    callback.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "graph",
    [
        {"only": []},
        {"a": [], "b": ["a"], "c": ["b"]},
        {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]},
        {"report": ["a", "b"], "a": [], "b": []},
    ],
)
def test_run_commits_graph_with_correct_initial_state(database, graph):
    workflow = Workflow("creation")
    callback = Mock(side_effect=AssertionError("Must not execute handlers"))
    for index, (name, dependencies) in enumerate(graph.items()):
        workflow.step(name, callback, depends_on=dependencies, retries=index)
    payload = {"company": "Stripe", "nested": {"values": [1, True, None]}}
    run_id = relay.run(workflow, payload, engine=database)
    assert isinstance(run_id, UUID)
    callback.assert_not_called()
    with Session(database) as session:
        stored = get_workflow_run(session, run_id)
        assert stored.workflow_name == "creation"
        assert stored.input == payload
        assert stored.status == WorkflowStatus.PENDING
        assert stored.started_at is None and stored.completed_at is None
        names = {step.id: step.step_name for step in stored.steps}
        assert set(names.values()) == set(graph)
        assert {
            (names[edge.step_run_id], names[edge.depends_on_step_run_id])
            for edge in stored.dependencies
        } == {(name, parent) for name, parents in graph.items() for parent in parents}
        for step in stored.steps:
            definition = workflow.steps[step.step_name]
            expected = StepStatus.PENDING if definition.depends_on else StepStatus.READY
            assert step.status == expected
            assert step.max_attempts == definition.max_attempts
            assert step.attempt_count == 0
            for field in (
                "input",
                "output",
                "error",
                "started_at",
                "completed_at",
                "lease_owner",
                "lease_expires_at",
                "next_retry_at",
            ):
                assert getattr(step, field) is None


@pytest.mark.integration
def test_repeated_calls_create_independent_runs(database):
    workflow = Workflow("repeat")
    workflow.step("first", Mock())
    workflow.step("second", Mock(), depends_on=["first"])
    first = relay.run(workflow, {"call": 1}, engine=database)
    second = relay.run(workflow, {"call": 2}, engine=database)
    assert first != second
    with Session(database) as session:
        a, b = get_workflow_run(session, first), get_workflow_run(session, second)
        assert a.input == {"call": 1} and b.input == {"call": 2}
        assert {step.id for step in a.steps}.isdisjoint(step.id for step in b.steps)
        assert a.dependencies[0].workflow_run_id == first
        assert b.dependencies[0].workflow_run_id == second


def counts(database):
    with Session(database) as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (WorkflowRun, StepRun, StepDependency)
        )


@pytest.mark.integration
def test_database_failure_rolls_back_complete_graph(database):
    workflow = Workflow("rollback")
    workflow.step("first", Mock())
    workflow.step("second", Mock(), depends_on=["first"])
    before = counts(database)

    # Force a real constraint error after the run, steps, and edges have been flushed.
    from app.services.runs import create_step_dependencies

    def fail_after_edges(session, workflow_run_id, dependencies):
        edges = create_step_dependencies(session, workflow_run_id, dependencies)
        edge = edges[0]
        session.add(
            StepDependency(
                workflow_run_id=workflow_run_id,
                step_run_id=edge.step_run_id,
                depends_on_step_run_id=edge.depends_on_step_run_id,
            )
        )
        session.flush()

    with (
        patch(
            "app.services.workflows.create_step_dependencies",
            side_effect=fail_after_edges,
        ),
        pytest.raises(IntegrityError),
    ):
        relay.run(workflow, {}, engine=database)
    assert counts(database) == before


@pytest.mark.integration
def test_default_engine_uses_settings_and_is_disposed(database, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", database.url.render_as_string(hide_password=False)
    )
    workflow = Workflow("configured")
    workflow.step("work", Mock())
    from app.db.connections import create_database_engine

    engines = []

    def track_engine(settings):
        engine = create_database_engine(settings)
        engine.dispose = Mock(wraps=engine.dispose)
        engines.append(engine)
        return engine

    with patch(
        "app.services.workflows.create_database_engine", side_effect=track_engine
    ):
        run_id = relay.run(workflow, {})
    engines[0].dispose.assert_called_once()
    with Session(database) as session:
        assert get_workflow_run(session, run_id).steps[0].status == StepStatus.READY


@pytest.mark.integration
def test_supplied_engine_remains_owned_by_caller(database):
    workflow = Workflow("caller_engine")
    workflow.step("work", Mock())
    with patch.object(database, "dispose", wraps=database.dispose) as dispose:
        relay.run(workflow, {}, engine=database)
        dispose.assert_not_called()
