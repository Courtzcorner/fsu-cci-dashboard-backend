"""
Shared content synchronization status: a single, cheap, authenticated
endpoint any logged-in user (admin OR alumni) can poll/subscribe to in
order to detect that an administrator changed shared data, without
needing to sign out or manually refresh.

Reads ONLY the small `content_versions` table (see
app.services.content_version_service.get_sync_status) - never
recalculates analytics, never loads alumni rows, never exposes any
private/per-user data. Marked `Cache-Control: no-store` so no
intermediary ever serves a stale cached copy of this response.
"""
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, get_current_user
from app.schemas.sync import SyncStatusOut
from app.services.content_version_service import get_sync_status

router = APIRouter(tags=["sync"])


@router.get("/sync/status", response_model=SyncStatusOut)
def sync_status(
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncStatusOut:
    response.headers["Cache-Control"] = "no-store"
    return SyncStatusOut(**get_sync_status(db))
