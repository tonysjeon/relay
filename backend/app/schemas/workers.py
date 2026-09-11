from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class WorkerResponse(BaseModel):
    id: str
    started_at: datetime
    last_heartbeat: datetime
    status: Literal["healthy", "unhealthy"]
