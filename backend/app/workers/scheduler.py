import argparse
import json
import logging
import time

from app.core.config import Settings
from app.db.connections import create_database_engine, create_redis_client
from app.services.queue import JOB_QUEUE
from app.services.retries import schedule_retries

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Queue retries whose persisted deadline is due"
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
            while True:
                count = schedule_retries(engine, redis, queue_name=args.queue)
                if count:
                    logger.info(json.dumps({"event": "retries_queued", "count": count}))
                if args.once:
                    return
                time.sleep(settings.retry_scan_interval_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            redis.close()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
