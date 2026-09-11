"""Register the same workflow definitions used by producers in this process."""

from app.workers.parallel import parallel_workflow
from app.workers.recovery_demo import recovery_workflow
from app.workflows import Workflow


def fetch(ctx):
    return {"company": ctx["workflow_input"]["company"]}


def report(ctx):
    return {"summary": ctx["step_outputs"]["fetch"]["company"]}


basic_workflow = Workflow("basic")
basic_workflow.step("fetch", fetch)
basic_workflow.step("report", report, depends_on=["fetch"])

workflow_registry = {
    basic_workflow.name: basic_workflow,
    parallel_workflow.name: parallel_workflow,
    recovery_workflow.name: recovery_workflow,
}
