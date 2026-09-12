import json
from unittest.mock import Mock, patch

import httpx
import pytest
from app import relay
from app.examples import openai_call
from app.services.execution import execute_step
from app.services.workflow_api import workflow_detail
from openai import OpenAI


@pytest.mark.integration
@pytest.mark.parametrize(
    "outcome", ["success", "unauthorized", "incomplete", "refused", "empty"]
)
def test_openai_request_and_tracking(database, monkeypatch, outcome):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    requests = []

    def handle(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body == {
            "model": "gpt-4.1-mini",
            "input": "Hello",
            "max_output_tokens": 512,
            "store": False,
        }
        if outcome == "unauthorized":
            return httpx.Response(
                401,
                json={
                    "error": {
                        "message": "test-key-not-real secret body",
                        "type": "invalid_request_error",
                    }
                },
            )
        content = {
            "type": "output_text",
            "text": "Hello back" if outcome != "empty" else "",
            "annotations": [],
        }
        if outcome == "refused":
            content = {"type": "refusal", "refusal": "Cannot answer"}
        return httpx.Response(
            200,
            json={
                "id": "resp_offline",
                "object": "response",
                "created_at": 0,
                "status": "incomplete" if outcome == "incomplete" else "completed",
                "model": "gpt-4.1-mini-2025-04-14",
                "output": [
                    {
                        "id": "msg_offline",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [content],
                    }
                ],
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 4,
                    "total_tokens": 7,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    def client(**kwargs):
        assert kwargs["max_retries"] == 0
        assert kwargs["timeout"] == 60
        return OpenAI(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle))
        )

    workflow = openai_call.openai_workflow
    run_id = relay.run(workflow, {"prompt": "Hello"}, engine=database, redis=Mock())
    step = workflow_detail(database, run_id).steps[0]
    with patch.object(openai_call, "OpenAI", side_effect=client):
        assert execute_step(database, step.id, {workflow.name: workflow})
    detail = workflow_detail(database, run_id)
    assert len(requests) == 1
    assert detail.steps[0].max_attempts == 1
    call = detail.steps[0].attempts[0].llm_calls[0]
    assert call.provider == "openai"
    assert call.input["input"] == "Hello"
    if outcome == "success":
        assert detail.status == call.status == "COMPLETED"
        assert call.output["text"] == "Hello back"
        assert call.input_tokens == 3 and call.output_tokens == 4
        assert call.output["response_id"] == "resp_offline"
    else:
        assert detail.status == call.status == "FAILED"
    assert "test-key-not-real" not in detail.model_dump_json()
    assert "secret body" not in detail.model_dump_json()


def test_missing_key_fails_before_saving_run(monkeypatch):
    from app.examples import __main__ as cli

    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr("sys.argv", ["examples", "openai"])
    with patch.object(cli.relay, "run") as submit, pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    submit.assert_not_called()


@pytest.mark.parametrize("prompt", ["", " ", "x" * 4001])
def test_invalid_prompt_fails_before_saving_run(monkeypatch, prompt):
    from app.examples import __main__ as cli

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setattr("sys.argv", ["examples", "openai", "--prompt", prompt])
    with patch.object(cli.relay, "run") as submit, pytest.raises(SystemExit):
        cli.main()
    submit.assert_not_called()
