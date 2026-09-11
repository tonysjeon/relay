import json
import logging
import socket
from contextlib import contextmanager
from threading import Event, Thread
from uuid import uuid4

from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Worker

logger = logging.getLogger(__name__)


def register_worker(engine: Engine) -> str:
    worker_id = f"worker-{socket.gethostname()}-{uuid4()}"
    with Session(engine) as session, session.begin():
        session.add(Worker(id=worker_id))
    return worker_id


def heartbeat(engine: Engine, worker_id: str) -> None:
    with Session(engine) as session, session.begin():
        updated = session.execute(
            update(Worker)
            .where(Worker.id == worker_id)
            .values(last_heartbeat=func.clock_timestamp())
        )
        if updated.rowcount != 1:
            raise LookupError(f"Worker {worker_id} is not registered")


@contextmanager
def worker_heartbeat(engine: Engine, interval: float):
    """Continue reporting liveness while a synchronous handler occupies the worker."""
    worker_id = register_worker(engine)
    stop = Event()

    def pulse():
        while not stop.wait(interval):
            try:
                heartbeat(engine, worker_id)
            except (SQLAlchemyError, LookupError):
                logger.exception(
                    json.dumps({"event": "heartbeat_failed", "worker_id": worker_id})
                )

    thread = Thread(target=pulse, name="worker-heartbeat", daemon=True)
    thread.start()
    logger.info(json.dumps({"event": "worker_started", "worker_id": worker_id}))
    try:
        yield worker_id
    finally:
        stop.set()
        thread.join()


def list_workers(engine: Engine, timeout: float) -> list[dict]:
    with Session(engine) as session:
        now = session.scalar(select(func.clock_timestamp()))
        workers = session.scalars(select(Worker).order_by(Worker.started_at, Worker.id))
        return [
            {
                "id": worker.id,
                "started_at": worker.started_at,
                "last_heartbeat": worker.last_heartbeat,
                "status": "healthy"
                if (now - worker.last_heartbeat).total_seconds() < timeout
                else "unhealthy",
            }
            for worker in workers
        ]
