from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from app.integrations.codex_hook import (
    connect_outbox,
    endpoint,
    enqueue,
    install,
    normalize,
)
from app.main import app
from app.schemas.activity import CodingEventInput
from app.services.activity import ingest_event, list_sessions, session_events
from fastapi.testclient import TestClient


def event(session="session-a", kind="SessionStart"):
    return CodingEventInput(
        event_id=uuid4(),
        session_id=session,
        event_type=kind,
        occurred_at=datetime.now(timezone.utc),
        cwd="/workspace/test",
        model="reported-model",
    )


def test_adapter_only_collects_allowed_metadata():
    value = normalize(
        {
            "session_id": "abc",
            "hook_event_name": "PostToolUse",
            "cwd": "/repo",
            "tool_name": "Bash",
            "tool_input": {"command": "secret"},
            "tool_response": "secret",
            "transcript_path": "/secret/file",
            "api_key": "secret",
        }
    )
    assert value["tool_name"] == "Bash"
    assert "secret" not in str(value)
    assert "content" not in value


def test_content_opt_in_redacts_common_credentials():
    payload = {
        "session_id": "abc",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "hello sk-secret123 password=hidden",
    }
    assert "content" not in normalize(payload)
    assert normalize(payload, True)["content"] == "hello [REDACTED] password=[REDACTED]"


def test_outbox_survives_reopen(tmp_path):
    path = tmp_path / "events.sqlite3"
    value = normalize({"session_id": "abc", "hook_event_name": "Stop"})
    connection = connect_outbox(path)
    enqueue(connection, value)
    enqueue(connection, value)
    connection.close()
    connection = connect_outbox(path)
    assert connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    connection.close()


def test_installer_preserves_other_hooks_and_is_idempotent(tmp_path):
    import json

    config = tmp_path / ".codex" / "hooks.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "existing"}]}]
                }
            }
        )
    )
    install(tmp_path, "http://localhost:8010", False)
    install(tmp_path, "http://localhost:8010", False)
    hooks = json.loads(config.read_text())["hooks"]
    assert len(hooks["Stop"]) == 2
    assert hooks["Stop"][0]["hooks"][0]["command"] == "existing"
    assert len(hooks["PreToolUse"]) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://remote.example",
        "http://localhost@remote.example",
        "http://localhost:8010/other",
    ],
)
def test_adapter_rejects_nonlocal_destinations(url):
    with pytest.raises(ValueError):
        endpoint(url)


@pytest.mark.integration
def test_event_deduplication_and_session_pagination(database):
    first = event("one")
    assert ingest_event(database, first)
    assert not ingest_event(database, first)
    for _ in range(3):
        ingest_event(database, event("one", "PostToolUse"))
    ingest_event(database, event("two"))
    sessions = list_sessions(database, 1, 0)
    assert sessions[0].session_id == "two"
    assert list_sessions(database, 1, 1)[0].event_count == 4
    latest = session_events(database, "one", None, 2)
    assert len(latest) == 2
    all_events = session_events(database, "one", 0, 10)
    assert len(all_events) == 4
    assert latest == all_events[-2:]
    assert session_events(database, "one", latest[-1].sequence, 10) == []


@pytest.mark.integration
def test_activity_api_schema_and_isolation(database):
    with (
        patch("app.main.create_database_engine", return_value=database),
        patch("app.main.create_redis_client", return_value=Mock()),
        TestClient(app) as client,
    ):
        payload = event("api-session").model_dump(mode="json")
        assert client.post("/coding-sessions/events", json=payload).json() == {
            "accepted": True
        }
        assert client.post("/coding-sessions/events", json=payload).json() == {
            "accepted": False
        }
        assert (
            client.post(
                "/coding-sessions/events", json={**payload, "tool_response": "secret"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/coding-sessions/events", json={**payload, "content": "x" * 8001}
            ).status_code
            == 422
        )
        events = client.get("/coding-sessions/api-session/events").json()
        assert len(events) == 1
        assert events[0]["session_id"] == "api-session"
        assert client.get("/coding-sessions/nonexistent/events").json() == []
        assert client.get("/coding-sessions?limit=10000").status_code == 422


def test_offline_delivery_retries_same_event_id(tmp_path):
    from app.integrations.codex_hook import flush

    path = tmp_path / "events.sqlite3"
    connection = connect_outbox(path)
    value = normalize({"session_id": "abc", "hook_event_name": "Stop"})
    enqueue(connection, value)
    opener = Mock()
    opener.open.side_effect = OSError("offline")
    with patch("app.integrations.codex_hook.build_opener", return_value=opener):
        flush(connection, "http://localhost:8010/coding-sessions/events")
    assert connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    opener.open.side_effect = None
    opener.open.return_value.__enter__ = Mock(return_value=Mock(status=200))
    opener.open.return_value.__exit__ = Mock(return_value=False)
    with patch("app.integrations.codex_hook.build_opener", return_value=opener):
        flush(connection, "http://localhost:8010/coding-sessions/events")
    import json

    assert (
        json.loads(opener.open.call_args.args[0].data)["event_id"] == value["event_id"]
    )
    assert connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    connection.close()
