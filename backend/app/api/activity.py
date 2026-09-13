from fastapi import APIRouter, Query, Request

from app.schemas.activity import (
    CodingEventInput,
    CodingEventResponse,
    CodingSessionResponse,
)
from app.services.activity import ingest_event, list_sessions, session_events

router = APIRouter(prefix="/coding-sessions", tags=["coding sessions"])


@router.post("/events")
def ingest(request: Request, body: CodingEventInput):
    return {"accepted": ingest_event(request.app.state.engine, body)}


@router.get("", response_model=list[CodingSessionResponse])
def sessions(
    request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
):
    return list_sessions(request.app.state.engine, limit, offset)


@router.get("/{session_id}/events", response_model=list[CodingEventResponse])
def events(
    request: Request,
    session_id: str,
    after: int | None = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=200),
):
    return session_events(request.app.state.engine, session_id, after, limit)
