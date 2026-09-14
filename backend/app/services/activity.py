from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.activity import CodingEvent
from app.schemas.activity import CodingEventResponse, CodingSessionResponse


def ingest_event(engine, event):
    with Session(engine) as session, session.begin():
        inserted = session.scalar(
            insert(CodingEvent)
            .values(**event.model_dump())
            .on_conflict_do_nothing(index_elements=[CodingEvent.event_id])
            .returning(CodingEvent.sequence)
        )
        return inserted is not None


def list_sessions(engine, limit, offset):
    # Latest received metadata is informational, not a claim that an agent is alive.
    grouped = (
        select(
            CodingEvent.session_id,
            func.max(CodingEvent.sequence).label("latest"),
            func.count().label("event_count"),
        )
        .group_by(CodingEvent.session_id)
        .subquery()
    )
    query = (
        select(CodingEvent, grouped.c.event_count)
        .join(grouped, grouped.c.latest == CodingEvent.sequence)
        .order_by(CodingEvent.sequence.desc())
        .limit(limit)
        .offset(offset)
    )
    with Session(engine) as session:
        return [
            CodingSessionResponse(
                session_id=e.session_id,
                cwd=e.cwd,
                model=e.model,
                event_count=count,
                last_event=e.event_type,
                last_seen=e.received_at,
            )
            for e, count in session.execute(query)
        ]


def session_events(engine, session_id, after, limit):
    query = select(CodingEvent).where(CodingEvent.session_id == session_id)
    if after is None:
        query = query.order_by(CodingEvent.sequence.desc())
    else:
        query = query.where(CodingEvent.sequence > after).order_by(CodingEvent.sequence)
    with Session(engine) as session:
        events = [
            CodingEventResponse.model_validate(e)
            for e in session.scalars(query.limit(limit))
        ]
    return list(reversed(events)) if after is None else events
