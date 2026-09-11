import os
import time

from app.workflows import Workflow


def fetch(ctx):
    return {"company": ctx["workflow_input"]["company"]}


def long_analysis(ctx):
    time.sleep(ctx["workflow_input"].get("seconds", 30))
    return {"company": ctx["step_outputs"]["fetch"]["company"], "pid": os.getpid()}


def report(ctx):
    return {"summary": ctx["step_outputs"]["long_analysis"]["company"]}


recovery_workflow = Workflow("crash_recovery")
recovery_workflow.step("fetch", fetch)
recovery_workflow.step("long_analysis", long_analysis, depends_on=["fetch"], retries=1)
recovery_workflow.step("report", report, depends_on=["long_analysis"])
