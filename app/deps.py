"""
Shared FastAPI dependencies: current-user resolution from a Bearer JWT,
and organization resolution for alumni/analytics endpoints.

Login credentials (and therefore identity + role) come from the
backend-only `data/users.csv` file (see app.csv_user_store). The JWT is
the signed source of truth for `username`/`role` on every subsequent
request - it is never re-read from the CSV on each call, so a request
only needs the token, not a DB/file round trip.

Organizations (used to segment alumni data) still live in the database.
Since CSV-based users are not assigned to specific organizations, any
authenticated user (admin or alumni) may currently view any organization's
dashboard - see the `organization` query param resolution below. Only the
`role` claim (currently "admin"/"alumni") gates admin-only actions such as
CSV import.
"""
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.organization import Organization
from app.security import TokenError, decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return CurrentUser(username=payload["sub"], role=payload["role"])


def get_organization_by_slug_for_current_user(
    organization: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Organization:
    """Resolve `?organization=` (falling back to DEFAULT_ORGANIZATION_SLUG)
    to an Organization row. Any authenticated user (admin or alumni role)
    may view any organization's dashboard for now - the role claim is kept
    on the token so the frontend/backend can add finer-grained,
    per-organization access later without another auth rework.
    """
    from app.config import get_settings

    _ = current_user  # currently unused for authorization, kept for parity with future per-org checks
    slug = organization or get_settings().default_organization_slug
    organization_record = db.query(Organization).filter(Organization.slug == slug).first()
    if organization_record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return organization_record


def require_admin_role(current_user: CurrentUser) -> None:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
