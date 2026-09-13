from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.activity import router as activity_router
from app.api.health import router
from app.api.workers import router as workers_router
from app.api.workflows import router as workflows_router
from app.core.config import Settings
from app.db.connections import create_database_engine, create_redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        redis = create_redis_client(settings)
        try:
            app.state.engine = engine
            app.state.settings = settings
            app.state.redis = redis
            yield
        finally:
            redis.close()
    finally:
        engine.dispose()


app = FastAPI(title="Relay", lifespan=lifespan)
app.include_router(router)
app.include_router(workers_router)
app.include_router(workflows_router)

app.include_router(activity_router)
