import argparse
import logging
import time
from collections.abc import Mapping

from redis import Redis
from sqlalchemy import Engine

from app.core.config import Settings
from app.db.connections import create_database_engine, create_redis_client
from app.services.execution import execute_step
from app.services.queue import JOB_QUEUE, dequeue_step
from app.services.workers import worker_heartbeat
from app.workers.registry import workflow_registry
from app.workflows import Workflow


def run_once(
    engine: Engine,
    redis: Redis,
    registry: Mapping[str, Workflow],
    *,
    queue_name: str = JOB_QUEUE,
) -> bool:
    step_id = dequeue_step(redis, queue_name=queue_name)
    if step_id is None:
        return False
    return execute_step(engine, step_id, registry, redis=redis, queue_name=queue_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute queued Relay steps")
    parser.add_argument(
        "--once", action="store_true", help="Consume at most one queued job and exit"
    )
    parser.add_argument("--queue", default=JOB_QUEUE, help="Redis list to consume")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        redis = create_redis_client(settings)
        try:
            with worker_heartbeat(engine, settings.worker_heartbeat_seconds):
                while True:
                    executed = run_once(
                        engine, redis, workflow_registry, queue_name=args.queue
                    )
                    if args.once:
                        return
                    if not executed:
                        time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            redis.close()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
