import time
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
from app.main import app
from app.models import Worker
from app.services.workers import (
    heartbeat,
    list_workers,
    register_worker,
    worker_heartbeat,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_registration_and_heartbeat(database):
    first, second = register_worker(database), register_worker(database)
    assert first != second and first.startswith("worker-")
    with Session(database) as session, session.begin():
        session.get(Worker, first).last_heartbeat = datetime.now(
            timezone.utc
        ) - timedelta(seconds=60)
    assert (
        next(row for row in list_workers(database, 30) if row["id"] == first)["status"]
        == "unhealthy"
    )
    heartbeat(database, first)
    assert (
        next(row for row in list_workers(database, 30) if row["id"] == first)["status"]
        == "healthy"
    )


def test_heartbeat_runs_during_work_and_stops_after_exit(database):
    with worker_heartbeat(database, 0.03) as worker_id:
        with Session(database) as session:
            initial = session.get(Worker, worker_id).last_heartbeat
        deadline = time.monotonic() + 3
        while True:
            with Session(database) as session:
                current = session.get(Worker, worker_id).last_heartbeat
            if current > initial:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
    with Session(database) as session:
        last = session.get(Worker, worker_id).last_heartbeat
    time.sleep(0.08)
    with Session(database) as session:
        assert session.get(Worker, worker_id).last_heartbeat == last


def test_workers_endpoint_classifies_stale_workers(database):
    worker_id = register_worker(database)
    with Session(database) as session, session.begin():
        session.get(Worker, worker_id).last_heartbeat = datetime.now(
            timezone.utc
        ) - timedelta(seconds=90)
    with (
        patch("app.main.create_database_engine", return_value=database),
        patch("app.main.create_redis_client", return_value=Mock()),
        TestClient(app) as client,
    ):
        response = client.get("/workers")
    assert response.status_code == 200
    row = next(row for row in response.json() if row["id"] == worker_id)
    assert row["status"] == "unhealthy"
    assert row["started_at"] and row["last_heartbeat"]
