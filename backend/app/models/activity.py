from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Identity, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CodingEvent(Base):
    __tablename__ = "coding_events"
    __table_args__ = (Index("ix_coding_session_sequence", "session_id", "sequence"),)

    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(unique=True)
    session_id: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(50))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    cwd: Mapped[str] = mapped_column(String(2000))
    model: Mapped[str | None] = mapped_column(String(200))
    turn_id: Mapped[str | None] = mapped_column(String(200))
    tool_name: Mapped[str | None] = mapped_column(String(200))
    tool_use_id: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[str | None] = mapped_column(Text)
