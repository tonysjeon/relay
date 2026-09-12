from app.core.config import Settings
from app.db.connections import create_redis_client
from app.workflows import Workflow


def request_company(ctx):
    # Simulate an external service that fails its first two requests. Redis keeps
    # that service's state across worker processes and restarts; it is separate
    # from Relay's persisted attempt counter and retry scheduling.
    key = f"relay:example:retry:{ctx['idempotency_key']}"
    client = create_redis_client(Settings())
    try:
        with client.pipeline() as transaction:
            transaction.incr(key)
            transaction.expire(key, 86400)
            requests, _ = transaction.execute()
    finally:
        client.close()
    if requests <= 2:
        raise RuntimeError(f"Simulated service unavailable (request {requests}/3)")
    return {"company": ctx["workflow_input"]["company"], "requests": requests}


def report(ctx):
    return {"summary": ctx["step_outputs"]["request_company"]["company"]}


retry_workflow = Workflow("retry_example")
retry_workflow.step("request_company", request_company, retries=2)
retry_workflow.step("report", report, depends_on=["request_company"])
