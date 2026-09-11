"""Small parallel workflow for observing two local worker processes."""

import os
import time

from app.workflows import Workflow


def fetch(ctx):
    return {"company": ctx["workflow_input"]["company"]}


def analyze_market(ctx):
    time.sleep(2)
    return {
        "company": ctx["step_outputs"]["fetch"]["company"],
        "score": 80,
        "pid": os.getpid(),
    }


def analyze_reviews(ctx):
    time.sleep(3)
    return {
        "company": ctx["step_outputs"]["fetch"]["company"],
        "score": 90,
        "pid": os.getpid(),
    }


def combine(ctx):
    market = ctx["step_outputs"]["analyze_market"]
    reviews = ctx["step_outputs"]["analyze_reviews"]
    return {
        "company": market["company"],
        "score": (market["score"] + reviews["score"]) / 2,
    }


parallel_workflow = Workflow("parallel")
parallel_workflow.step("fetch", fetch)
parallel_workflow.step("analyze_market", analyze_market, depends_on=["fetch"])
parallel_workflow.step("analyze_reviews", analyze_reviews, depends_on=["fetch"])
parallel_workflow.step(
    "combine", combine, depends_on=["analyze_market", "analyze_reviews"]
)
