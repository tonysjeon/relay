from unittest.mock import Mock

import pytest
from app.workers import scheduler
from redis.exceptions import ConnectionError


@pytest.mark.parametrize("once", [False, True])
def test_scheduler_retries_infrastructure_errors_or_reports_once_failure(
    monkeypatch, once
):
    engine, redis = Mock(), Mock()
    retry = Mock(side_effect=[ConnectionError("offline"), 0])
    recovery, ready = Mock(return_value=0), Mock(return_value=0)
    monkeypatch.setattr("sys.argv", ["scheduler", "--once"] if once else ["scheduler"])
    monkeypatch.setattr(scheduler, "create_database_engine", lambda _: engine)
    monkeypatch.setattr(scheduler, "create_redis_client", lambda _: redis)
    monkeypatch.setattr(scheduler, "schedule_retries", retry)
    monkeypatch.setattr(scheduler, "recover_abandoned_steps", recovery)
    monkeypatch.setattr(scheduler, "reconcile_ready_steps", ready)
    for scan in (retry, recovery, ready):
        scan.__name__ = "scan"
    clock = [0.0]
    monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])

    def sleep(_):
        if clock[0]:
            raise KeyboardInterrupt
        clock[0] = 10.0

    monkeypatch.setattr(scheduler.time, "sleep", sleep)
    if once:
        with pytest.raises(ConnectionError):
            scheduler.main()
    else:
        scheduler.main()
        assert retry.call_count == recovery.call_count == ready.call_count == 2
    engine.dispose.assert_called_once()
    redis.close.assert_called_once()
