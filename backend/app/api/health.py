from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.health import dependencies_healthy

router = APIRouter()


@router.get("/health")
def health(request: Request) -> JSONResponse:
    if dependencies_healthy(request.app.state.engine, request.app.state.redis):
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "unavailable"}, status_code=503)
