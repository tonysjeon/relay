"""Register the same workflow definitions used by producers in this process."""

from app.examples.linear import linear_workflow
from app.examples.openai_call import openai_workflow
from app.examples.retry import retry_workflow
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
    openai_workflow.name: openai_workflow,
    linear_workflow.name: linear_workflow,
    retry_workflow.name: retry_workflow,
    basic_workflow.name: basic_workflow,
    parallel_workflow.name: parallel_workflow,
    recovery_workflow.name: recovery_workflow,
}
