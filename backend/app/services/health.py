from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError


def dependencies_healthy(engine: Engine, redis: Redis) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False

    try:
        return bool(redis.ping())
    except RedisError:
        return False
