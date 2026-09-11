import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from app import relay
from app.core.config import Settings
from app.db.connections import create_redis_client
from app.models import StepStatus
from app.services.queue import enqueue_steps
from app.services.runs import get_workflow_run
from parallel_worker import workflow
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def wait_for(condition, processes):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        assert all(process.poll() is None for process in processes), (
            "A worker exited unexpectedly"
        )
        if condition():
            return
        time.sleep(0.02)
    pytest.fail("Timed out waiting for parallel workers")


@pytest.mark.parametrize("first", ["b", "c"])
def test_two_processes_overlap_and_wait_for_both_branches(database, tmp_path, first):
    client = create_redis_client(Settings())
    key = f"relay:test:parallel:{uuid4().hex}"
    backend = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "DATABASE_URL": database.url.render_as_string(hide_password=False),
        "REDIS_URL": Settings().redis_url,
        "PYTHONPATH": str(backend),
    }
    command = [
        sys.executable,
        str(Path(__file__).with_name("parallel_worker.py")),
        "--queue",
        key,
    ]
    processes, logs = [], []

    def steps():
        with Session(database) as session:
            return {
                step.step_name: step for step in get_workflow_run(session, run_id).steps
            }

    try:
        run_id = relay.run(
            workflow,
            {"value": 5, "directory": str(tmp_path)},
            engine=database,
            redis=client,
            queue_name=key,
        )
        subprocess.run(
            [*command, "--once"],
            env=env,
            cwd=backend,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        initial = steps()
        assert initial["a"].status == StepStatus.COMPLETED
        assert initial["b"].status == initial["c"].status == StepStatus.READY
        assert initial["d"].status == StepStatus.PENDING
        expected = {str(initial[name].id) for name in ("b", "c")}
        assert {
            json.loads(message)["step_run_id"] for message in client.lrange(key, 0, -1)
        } == expected
        assert client.llen(key) == 2
        # Duplicate branch jobs must not cause a second handler execution.
        enqueue_steps(client, [initial["b"].id, initial["c"].id], queue_name=key)
        for index in range(2):
            log = (tmp_path / f"worker-{index}.log").open("w+")
            logs.append(log)
            processes.append(
                subprocess.Popen(
                    command, env=env, cwd=backend, stdout=log, stderr=subprocess.STDOUT
                )
            )
        wait_for(
            lambda: all((tmp_path / f"{name}.started").exists() for name in ("b", "c")),
            processes,
        )
        pids = {int((tmp_path / f"{name}.started").read_text()) for name in ("b", "c")}
        assert pids == {process.pid for process in processes}
        overlapping = steps()
        assert overlapping["b"].status == overlapping["c"].status == StepStatus.RUNNING
        assert overlapping["d"].status == StepStatus.PENDING

        (tmp_path / f"{first}.release").touch()
        wait_for(lambda: steps()[first].status == StepStatus.COMPLETED, processes)
        remaining = "c" if first == "b" else "b"
        midway = steps()
        assert midway[remaining].status == StepStatus.RUNNING
        assert midway["d"].status == StepStatus.PENDING
        assert all(
            json.loads(message)["step_run_id"] != str(initial["d"].id)
            for message in client.lrange(key, 0, -1)
        )

        (tmp_path / f"{remaining}.release").touch()
        wait_for(
            lambda: all(
                step.status == StepStatus.COMPLETED for step in steps().values()
            ),
            processes,
        )
        finished = steps()
        assert all(step.attempt_count == 1 for step in finished.values())
        assert finished["d"].output == {"total": 12, "parents": ["b", "c"]}
        assert finished["d"].started_at >= max(
            finished[name].completed_at for name in ("b", "c")
        )
        assert max(finished[name].started_at for name in ("b", "c")) < min(
            finished[name].completed_at for name in ("b", "c")
        )
        wait_for(lambda: client.llen(key) == 0, processes)
    finally:
        for process in processes:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.seek(0)
            print(log.read())
            log.close()
        client.delete(key)
        client.close()
