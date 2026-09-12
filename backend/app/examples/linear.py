from app.workflows import Workflow


def fetch(ctx):
    return {"company": ctx["workflow_input"]["company"]}


def transform(ctx):
    return {"company": ctx["step_outputs"]["fetch"]["company"].strip().upper()}


def save(ctx):
    # Relay persists this output; no external storage is needed for the example.
    return {"record": ctx["step_outputs"]["transform"]}


linear_workflow = Workflow("linear_example")
linear_workflow.step("fetch", fetch)
linear_workflow.step("transform", transform, depends_on=["fetch"])
linear_workflow.step("save", save, depends_on=["transform"])
