from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest
from app.workflows import StepDefinition, Workflow


def handler(ctx):
    return ctx


def test_linear_workflow_and_retry_configuration():
    workflow = Workflow("research")
    fetch = workflow.step("fetch", handler)
    analyze = workflow.step("analyze", handler, depends_on=["fetch"], retries=3)
    workflow.step("report", handler, depends_on=["analyze"])
    workflow.validate()
    assert workflow.name == "research"
    assert list(workflow.steps) == ["fetch", "analyze", "report"]
    assert fetch.handler is handler
    assert fetch.max_attempts == 1
    assert analyze.max_attempts == 4
    assert analyze.depends_on == ("fetch",)


def test_parallel_branches_with_fan_in_and_independent_root():
    workflow = Workflow("parallel")
    workflow.step("fetch", handler)
    workflow.step("left", handler, depends_on=["fetch"])
    workflow.step("right", handler, depends_on=["fetch"])
    workflow.step("combine", handler, depends_on=["left", "right"])
    workflow.step("independent", handler)
    workflow.validate()


def test_forward_references_and_revalidation():
    workflow = Workflow("forward")
    workflow.step("report", handler, depends_on=["fetch"])
    with pytest.raises(ValueError, match="'report' depends on missing step 'fetch'"):
        workflow.validate()
    workflow.step("fetch", handler)
    workflow.validate()
    workflow.step("extra", handler, depends_on=["unknown"])
    with pytest.raises(ValueError, match="missing step 'unknown'"):
        workflow.validate()


def test_duplicate_step_does_not_replace_original():
    workflow = Workflow("duplicate")
    original = workflow.step("work", handler)
    with pytest.raises(ValueError, match="Duplicate step name"):
        workflow.step("work", Mock())
    assert workflow.steps["work"] is original


@pytest.mark.parametrize(
    "graph",
    [
        {"a": ["a"]},
        {"a": ["b"], "b": ["a"]},
        {"root": [], "a": ["b"], "b": ["c"], "c": ["a"], "tail": ["c"]},
    ],
)
def test_cycles_including_disconnected_cycles(graph):
    workflow = Workflow("cycle")
    for name, dependencies in graph.items():
        workflow.step(name, handler, depends_on=dependencies)
    with pytest.raises(ValueError, match="Circular dependency"):
        workflow.validate()


def test_validation_does_not_execute_handlers():
    callback = Mock(side_effect=AssertionError("Must not execute"))
    workflow = Workflow("definition_only")
    workflow.step("first", callback)
    workflow.validate()
    workflow.validate()
    callback.assert_not_called()


def test_long_chain_does_not_hit_python_recursion_limit():
    workflow = Workflow("long")
    for index in range(1500):
        workflow.step(str(index), handler, depends_on=[str(index - 1)] if index else [])
    workflow.validate()


def test_dependency_list_is_copied_and_definitions_are_read_only():
    dependencies = ["fetch"]
    workflow = Workflow("immutable")
    workflow.step("fetch", handler)
    step = workflow.step("report", handler, depends_on=dependencies)
    dependencies.append("missing")
    workflow.validate()
    assert step.depends_on == ("fetch",)
    with pytest.raises(FrozenInstanceError):
        step.max_attempts = 0
    with pytest.raises(TypeError):
        workflow.steps["report"] = step


@pytest.mark.parametrize("retries", [-1, 1.5, True, "3"])
def test_invalid_retries_do_not_add_step(retries):
    workflow = Workflow("invalid")
    with pytest.raises(ValueError, match="retries"):
        workflow.step("work", handler, retries=retries)
    assert not workflow.steps


@pytest.mark.parametrize("name", ["", "   ", None])
def test_invalid_names(name):
    with pytest.raises(ValueError, match="non-empty strings"):
        Workflow(name)
    with pytest.raises(ValueError, match="non-empty strings"):
        Workflow("valid").step(name, handler)


@pytest.mark.parametrize("dependencies", ["fetch", ["fetch", "fetch"], [""]])
def test_invalid_dependency_lists(dependencies):
    with pytest.raises((TypeError, ValueError)):
        Workflow("invalid").step("work", handler, depends_on=dependencies)


def test_empty_workflow_and_non_callable_handler():
    with pytest.raises(ValueError, match="at least one step"):
        Workflow("empty").validate()
    with pytest.raises(TypeError, match="must be callable"):
        Workflow("invalid").step("work", None)


def test_standalone_step_definition():
    step = StepDefinition("work", handler, max_attempts=2)
    assert step.max_attempts == 2
    assert step.depends_on == ()
    with pytest.raises(ValueError, match="max_attempts"):
        StepDefinition("work", handler, max_attempts=0)
