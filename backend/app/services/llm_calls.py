"""Explicit, provider-independent model-call tracking inside synchronous handlers."""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from app.models import LLMCall, StepAttempt, StepRun, WorkflowRun
from app.services.leases import lease_conditions
from app.services.pricing import estimate_cost, pricing_snapshot


@dataclass(frozen=True)
class Execution:
    engine: Engine
    run_id: UUID
    step_id: UUID
    owner: str
    attempt: int


_current: ContextVar[Execution | None] = ContextVar("relay_execution", default=None)


@contextmanager
def track_execution(engine, run_id, step_id, owner, attempt):
    token = _current.set(Execution(engine, run_id, step_id, owner, attempt))
    try:
        yield
    finally:
        _current.reset(token)


def _json(value):
    # Validate and snapshot caller-owned data before handing control back.
    return json.loads(json.dumps(value, allow_nan=False))


class CallResult:
    def __init__(self, pricing=None):
        self.pricing = pricing
        self.values = None
        self.active = True

    def set_result(
        self,
        output: Any,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
    ) -> None:
        if not self.active or self.values is not None:
            raise RuntimeError("Set the call result once, inside its context")
        for value in (input_tokens, output_tokens, cached_input_tokens):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Token counts must be non-negative integers or None")
        if cached_input_tokens is not None and (
            input_tokens is None or cached_input_tokens > input_tokens
        ):
            raise ValueError("Cached tokens must be a subset of input tokens")
        self.values = {
            "cached_input_tokens": cached_input_tokens,
            "estimated_cost_usd": estimate_cost(
                self.pricing, input_tokens, output_tokens, cached_input_tokens
            ),
            "output": _json(output),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }


def _lock_step(session, execution):
    session.scalar(
        select(WorkflowRun.id)
        .where(WorkflowRun.id == execution.run_id)
        .with_for_update()
    )
    return session.scalar(
        select(StepRun.id).where(
            *lease_conditions(execution.step_id, execution.owner, execution.attempt)
        )
    )


def _finish(execution, call_id, status, values):
    with Session(execution.engine) as session, session.begin():
        if _lock_step(session, execution) is None:
            return
        session.execute(
            update(LLMCall)
            .where(
                LLMCall.id == call_id,
                LLMCall.status == "RUNNING",
                select(StepRun.id)
                .where(
                    *lease_conditions(
                        execution.step_id, execution.owner, execution.attempt
                    )
                )
                .exists(),
            )
            .values(status=status, completed_at=func.clock_timestamp(), **values)
        )


@contextmanager
def llm_call(*, provider: str, model: str, input: Any, service_tier: str | None = None):
    """Wrap one provider call; explicitly record its response and optional usage."""
    execution = _current.get()
    if execution is None:
        raise RuntimeError("relay.llm_call must run inside a Relay step handler")
    if (
        not isinstance(provider, str)
        or not provider.strip()
        or not isinstance(model, str)
        or not model.strip()
    ):
        raise ValueError("provider and model must be non-empty strings")
    payload = _json(input)
    pricing = pricing_snapshot(provider, model, service_tier)
    with Session(execution.engine) as session, session.begin():
        if _lock_step(session, execution) is None:
            raise RuntimeError("Cannot start an LLM call after losing the step lease")
        attempt_id = session.scalar(
            select(StepAttempt.id).where(
                StepAttempt.step_run_id == execution.step_id,
                StepAttempt.attempt_number == execution.attempt,
                StepAttempt.worker_id == execution.owner,
                StepAttempt.status == "RUNNING",
            )
        )
        if attempt_id is None:
            raise RuntimeError("Current step attempt is missing")
        call = LLMCall(
            step_attempt_id=attempt_id,
            provider=provider,
            model=model,
            input=payload,
            pricing=pricing,
            status="RUNNING",
        )
        session.add(call)
        session.flush()
        call_id = call.id
    result = CallResult(pricing)
    try:
        yield result
        if result.values is None:
            raise RuntimeError("Call set_result before leaving relay.llm_call")
    except Exception as exc:
        # Avoid copying provider response bodies or credentials into call errors.
        _finish(execution, call_id, "FAILED", {"error": type(exc).__name__})
        raise
    else:
        _finish(execution, call_id, "COMPLETED", result.values)
    finally:
        result.active = False
