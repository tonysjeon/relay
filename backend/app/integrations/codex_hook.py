"""Dependency-free, advisory Codex hook. Never reads transcripts or tool bodies."""

import argparse
import json
import os
import re
import shlex
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

EVENTS = (
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "Interrupt",
)
MARKER = "Record activity in Relay"


def normalize(payload, capture_content=False):
    if not isinstance(payload, dict) or payload.get("hook_event_name") not in EVENTS:
        raise ValueError("Unsupported hook")
    if not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
        raise ValueError("Missing session id")
    result = {
        "event_id": str(uuid4()),
        "session_id": payload["session_id"][:200],
        "event_type": payload["hook_event_name"],
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "cwd": str(payload.get("cwd", ""))[:2000],
    }
    for field in ("model", "turn_id", "tool_name", "tool_use_id"):
        if isinstance(payload.get(field), str):
            result[field] = payload[field][:200]
    if capture_content:
        field = {"UserPromptSubmit": "prompt", "Stop": "last_assistant_message"}.get(
            result["event_type"]
        )
        value = payload.get(field) if field else None
        if isinstance(value, str):
            # Best-effort redaction only. Content capture is explicitly opt-in.
            value = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", value)
            value = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", value)
            value = re.sub(
                r"(?i)((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[^\s,;]+",
                r"\1[REDACTED]",
                value,
            )
            result["content"] = value[:8000]
    return result


def endpoint(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("Relay URL must be a local HTTP origin")
    return value.rstrip("/") + "/coding-sessions/events"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def connect_outbox(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(path, timeout=0.1)
    os.chmod(path, 0o600)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    return connection


def enqueue(connection, event):
    with connection:
        connection.execute(
            "INSERT OR IGNORE INTO outbox VALUES (?, ?)",
            (event["event_id"], json.dumps(event)),
        )
        # Bound disk usage during extended outages.
        connection.execute(
            "DELETE FROM outbox WHERE rowid NOT IN (SELECT rowid FROM outbox ORDER BY rowid DESC LIMIT 10000)"
        )


def flush(connection, url):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + 1.2
    for event_id, payload in connection.execute(
        "SELECT id, payload FROM outbox ORDER BY rowid LIMIT 20"
    ).fetchall():
        if time.monotonic() >= deadline:
            break
        try:
            with opener.open(
                Request(
                    url,
                    data=payload.encode(),
                    headers={"Content-Type": "application/json"},
                ),
                timeout=0.3,
            ) as response:
                if response.status != 200:
                    break
        except HTTPError as exc:
            if exc.code != 422:
                break
            # A permanently invalid event must not block later deliveries.
        except OSError:
            break
        with connection:
            connection.execute("DELETE FROM outbox WHERE id = ?", (event_id,))


def install(project, url, capture_content):
    endpoint(url)
    project = project.resolve()
    config = project / ".codex" / "hooks.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(config.read_text()) if config.exists() else {}
    command = " ".join(
        shlex.quote(part)
        for part in [
            sys.executable,
            str(Path(__file__).resolve()),
            "--url",
            url,
            "--outbox",
            str(project / ".relay" / "coding-events.sqlite3"),
        ]
    )
    if capture_content:
        command += " --capture-content"
    hooks = data.setdefault("hooks", {})
    for event in EVENTS:
        groups = hooks.setdefault(event, [])
        for group in groups:
            group["hooks"] = [
                h for h in group.get("hooks", []) if h.get("statusMessage") != MARKER
            ]
        groups[:] = [g for g in groups if g.get("hooks")]
        groups.append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": command,
                        "timeout": 3,
                        "statusMessage": MARKER,
                    }
                ]
            }
        )
    config.write_text(json.dumps(data, indent=2) + "\n")
    print(
        f"Installed Relay hooks in {config}. Review and trust them in Codex before use."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", type=Path, metavar="PROJECT")
    parser.add_argument("--url", default="http://localhost:8010")
    parser.add_argument(
        "--outbox", type=Path, default=Path(".relay/coding-events.sqlite3")
    )
    parser.add_argument("--capture-content", action="store_true")
    parser.add_argument("--flush", action="store_true")
    args = parser.parse_args()
    if args.install:
        install(args.install, args.url, args.capture_content)
        return
    try:
        url = endpoint(args.url)
        connection = connect_outbox(args.outbox)
        try:
            if not args.flush:
                raw = sys.stdin.read(1_048_577)
                if len(raw) > 1_048_576:
                    return
                enqueue(connection, normalize(json.loads(raw), args.capture_content))
            flush(connection, url)
        finally:
            connection.close()
    except Exception:  # noqa: BLE001, S110 -- advisory hook must not affect Codex
        # Observability must never block or steer the coding task.
        pass
    finally:
        if not args.flush:
            print("{}")


if __name__ == "__main__":
    main()
