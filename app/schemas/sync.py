from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SyncStatusOut(BaseModel):
    """Response shape for GET /sync/status. Contains only integer
    version counters and a timestamp - never any alumni, analytics,
    event, speaker, superstar, or profile data itself."""

    global_version: int
    updated_at: Optional[datetime] = None
    domains: dict[str, int]
