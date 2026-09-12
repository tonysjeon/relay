import argparse
import json
import logging
import time

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.db.connections import create_database_engine, create_redis_client
from app.services.dispatch import reconcile_ready_steps
from app.services.queue import JOB_QUEUE
from app.services.recovery import recover_abandoned_steps
from app.services.retries import schedule_retries
from app.services.workflows import QueueDispatchError

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Queue due retries and recover abandoned steps"
    )
    parser.add_argument("--once", action="store_true", help="Scan once and exit")
    parser.add_argument("--queue", default=JOB_QUEUE)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        redis = create_redis_client(settings)
        try:
            scans = [
                (schedule_retries, settings.retry_scan_interval_seconds),
                (recover_abandoned_steps, settings.lease_scan_interval_seconds),
                (reconcile_ready_steps, settings.ready_scan_interval_seconds),
            ]
            deadlines = [0.0] * len(scans)
            while True:
                for index, (scan, interval) in enumerate(scans):
                    if time.monotonic() < deadlines[index]:
                        continue
                    try:
                        count = scan(engine, redis, queue_name=args.queue)
                        if count:
                            logger.info(
                                json.dumps({"event": scan.__name__, "count": count})
                            )
                    except (RedisError, SQLAlchemyError, QueueDispatchError):
                        if args.once:
                            raise
                        logger.exception(
                            "Scheduler scan failed: %s; will retry", scan.__name__
                        )
                    deadlines[index] = time.monotonic() + interval
                if args.once:
                    return
                time.sleep(max(0, min(deadlines) - time.monotonic()))
        except KeyboardInterrupt:
            pass
        finally:
            redis.close()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
