"""Phase 1 multi-institution infrastructure: lets an authenticated user
discover which dashboard contexts (organizations) they may switch into.

Read-only - never creates/modifies a UserOrganization row, never touches
the user's session/token. See app.services.organization_context_service
for the exact legacy-fallback and filtering rules.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, get_current_user
from app.schemas.organization import AvailableContextsResponse
from app.services.organization_context_service import build_available_contexts

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("/available-contexts", response_model=AvailableContextsResponse)
def get_available_contexts(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AvailableContextsResponse:
    return AvailableContextsResponse(contexts=build_available_contexts(db, current_user))
