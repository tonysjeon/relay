from fastapi import APIRouter, Request

from app.schemas.workers import WorkerResponse
from app.services.workers import list_workers

router = APIRouter()


@router.get("/workers", response_model=list[WorkerResponse])
def workers(request: Request):
    return list_workers(
        request.app.state.engine, request.app.state.settings.worker_timeout_seconds
    )
