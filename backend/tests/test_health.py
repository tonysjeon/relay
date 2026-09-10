from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

from app.main import app


@pytest.mark.parametrize("failure", [None, "postgres", "redis"])
def test_health_and_connection_cleanup(failure):
    engine = MagicMock()
    redis = MagicMock()
    redis.ping.return_value = True
    if failure == "postgres":
        engine.connect.side_effect = OperationalError("SELECT 1", {}, Exception("offline"))
    elif failure == "redis":
        redis.ping.side_effect = RedisConnectionError("offline")

    with (
        patch("app.main.create_database_engine", return_value=engine),
        patch("app.main.create_redis_client", return_value=redis),
        TestClient(app) as client,
    ):
        response = client.get("/health")
        assert response.status_code == (503 if failure else 200)
        assert response.json() == {"status": "unavailable" if failure else "ok"}

    engine.dispose.assert_called_once()
    redis.close.assert_called_once()


@pytest.mark.integration
def test_health_with_live_services():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
