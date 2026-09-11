"""Register the same workflow definitions used by producers in this process."""

from app.workflows import Workflow


def fetch(ctx):
    return {"company": ctx["workflow_input"]["company"]}


def report(ctx):
    return {"summary": ctx["step_outputs"]["fetch"]["company"]}


basic_workflow = Workflow("basic")
basic_workflow.step("fetch", fetch)
basic_workflow.step("report", report, depends_on=["fetch"])

workflow_registry = {basic_workflow.name: basic_workflow}
