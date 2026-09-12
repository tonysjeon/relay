"""A single real model request to verify Relay's LLM tracking."""

from openai import APIError, OpenAI

from app import relay
from app.core.config import Settings
from app.workflows import Workflow


class OpenAITestError(RuntimeError):
    pass


def require_openai_settings() -> Settings:
    settings = Settings()
    if (
        not settings.openai_api_key
        or not settings.openai_api_key.get_secret_value().strip()
    ):
        raise OpenAITestError(
            "Set OPENAI_API_KEY in .env and recreate the API/worker containers"
        )
    return settings


def generate(ctx):
    settings = require_openai_settings()
    prompt = ctx["workflow_input"]["prompt"]
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise ValueError("prompt must contain 1 to 4000 characters")
    request = {"input": prompt, "max_output_tokens": 512, "store": False}
    with relay.llm_call(
        provider="openai", model=settings.openai_model, input=request
    ) as call:
        try:
            with OpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                max_retries=0,
                timeout=settings.openai_timeout_seconds,
            ) as client:
                response = client.responses.create(
                    model=settings.openai_model, **request
                )
        except APIError as exc:
            # Do not persist provider bodies, which can echo credentials or input.
            raise OpenAITestError(
                f"OpenAI request failed: {type(exc).__name__} (HTTP {getattr(exc, 'status_code', None)})"
            ) from None
        if response.status != "completed" or not response.output_text.strip():
            raise OpenAITestError(
                "OpenAI returned an incomplete, refused, or empty response"
            )
        result = {
            "text": response.output_text,
            "response_id": response.id,
            "model": response.model,
        }
        call.set_result(
            result,
            input_tokens=response.usage.input_tokens if response.usage else None,
            output_tokens=response.usage.output_tokens if response.usage else None,
        )
    return result


openai_workflow = Workflow("openai_test")
# No retries: this is a one-request smoke test, not a resilience demo.
openai_workflow.step("generate", generate)
