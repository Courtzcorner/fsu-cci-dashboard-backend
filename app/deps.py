"""
Shared FastAPI dependencies: current-user resolution from a Bearer JWT
(re-fetched from the `users` table on every request, so role/alumni_id
changes take effect immediately), and organization resolution for
alumni/content endpoints.
"""
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.security import TokenError, decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    username: str
    role: str
    alumni_id: str | None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
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

    user = db.query(User).filter(User.username == payload["sub"]).first()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists or is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Role/alumni_id are always read fresh from the database rather than
    # trusted from the (possibly stale) JWT claims, so an admin change to
    # either takes effect on the very next request.
    return CurrentUser(id=user.id, username=user.username, role=user.role, alumni_id=user.alumni_id)


def get_organization_by_slug_for_current_user(
    organization: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Organization:
    """Resolve `?organization=` (falling back to DEFAULT_ORGANIZATION_SLUG)
    to an Organization row. Any authenticated user (admin or alumni) may
    view any organization's published content for now.
    """
    from app.config import get_settings

    _ = current_user  # kept for parity with future per-org access checks
    slug = organization or get_settings().default_organization_slug
    organization_record = db.query(Organization).filter(Organization.slug == slug).first()
    if organization_record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return organization_record


def require_admin_role(current_user: CurrentUser) -> None:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")


def require_alumni_profile(current_user: CurrentUser) -> str:
    """Returns the current user's linked alumni_id, or 404 if this account
    has no associated alumni profile."""
    if not current_user.alumni_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This account has no associated alumni profile",
        )
    return current_user.alumni_id
