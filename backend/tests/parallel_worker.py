"""Test-only registry with filesystem gates, executed in fresh worker processes."""

import os
import time
from pathlib import Path

from app.workers import worker
from app.workflows import Workflow


def fetch(ctx):
    return {"value": ctx["workflow_input"]["value"]}


def branch(ctx, name):
    directory = Path(ctx["workflow_input"]["directory"])
    (directory / f"{name}.started").write_text(str(os.getpid()))
    deadline = time.monotonic() + 20
    while not (directory / f"{name}.release").exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"Branch {name} was not released")
        time.sleep(0.02)
    return {"value": ctx["step_outputs"]["a"]["value"] + 1, "pid": os.getpid()}


def left(ctx):
    return branch(ctx, "b")


def right(ctx):
    return branch(ctx, "c")


def combine(ctx):
    return {
        "total": sum(output["value"] for output in ctx["step_outputs"].values()),
        "parents": sorted(ctx["step_outputs"]),
    }


workflow = Workflow("process_test")
workflow.step("a", fetch)
workflow.step("b", left, depends_on=["a"])
workflow.step("c", right, depends_on=["a"])
workflow.step("d", combine, depends_on=["b", "c"])


if __name__ == "__main__":
    worker.workflow_registry = {workflow.name: workflow}
    worker.main()
