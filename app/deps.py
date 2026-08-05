"""
Shared FastAPI dependencies: current-user resolution from a Bearer JWT
(re-fetched from the `users` table on every request, so role/alumni_id
changes take effect immediately), and organization resolution for
alumni/content endpoints.
"""
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.organization import Organization
from app.models.roles import UserRole, resolve_effective_role
from app.models.user import User
from app.services.temporary_alumni_context_policy import is_temporary_alumni_compatibility_slug
from app.models.user_organization import UserOrganization
from app.security import TokenError, decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    username: str
    role: str
    alumni_id: str | None
    must_change_credentials: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _resolve_current_user(
    credentials: HTTPAuthorizationCredentials, db: Session
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

    # Tokens minted before the `tv` claim existed carry no "tv" value at
    # all - treated as 0, which matches every pre-existing user's default
    # `token_version`, so no session issued before this feature shipped
    # is broken. A token is only ever rejected here if `token_version` has
    # since been bumped (POST /auth/logout, or completing first-login
    # credential setup) - i.e. it was deliberately revoked.
    if payload.get("tv", 0) != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Role/alumni_id are always read fresh from the database rather than
    # trusted from the (possibly stale) JWT claims, so an admin change to
    # either takes effect on the very next request.
    return CurrentUser(
        id=user.id,
        username=user.username,
        role=user.role,
        alumni_id=user.alumni_id,
        must_change_credentials=user.must_change_credentials,
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """The dependency used by every dashboard/profile/analytics/admin
    route. A temporary account that has not yet completed
    POST /auth/complete-first-login is authenticated but explicitly
    denied here - server-side, not just by a frontend redirect - so its
    token cannot be used against any route other than the three exempted
    ones in app.routers.auth_routes that use
    `get_current_user_allow_pending_credentials` instead."""
    current_user = _resolve_current_user(credentials, db)
    if current_user.must_change_credentials:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Credential setup required before accessing this resource",
        )
    return current_user


def get_current_user_allow_pending_credentials(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """Identical to get_current_user, but does NOT block accounts that
    still have must_change_credentials=True. Only ever used by
    GET /auth/me, POST /auth/complete-first-login, and POST /auth/logout."""
    return _resolve_current_user(credentials, db)


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


@dataclass(frozen=True)
class OrganizationAccess:
    """Result of resolving+authorizing an organization context for the
    current user (see get_authorized_organization below)."""

    organization: Organization
    effective_role: str
    membership: Optional[UserOrganization]
    # True only for a legacy account with zero UserOrganization rows at
    # all - i.e. one still running under the temporary rollout-compatible
    # fallback described in app.services.organization_context_service,
    # not the final per-organization authorization model.
    is_legacy_access: bool


def get_authorized_organization(
    organization: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrganizationAccess:
    """Phase 1 authorization dependency - NOT yet wired into any existing
    endpoint (see app.routers.organization_routes for its only current
    caller). Resolves `?organization=` (falling back to
    DEFAULT_ORGANIZATION_SLUG when omitted, exactly like
    get_organization_by_slug_for_current_user) and then authorizes it:

    - A user with at least one UserOrganization row is restricted to
      exactly their memberships; any other organization is a 403.
    - A user with zero UserOrganization rows at all (every account today,
      until a future backfill) keeps today's unrestricted behavior -
      this is TEMPORARY ROLLOUT COMPATIBILITY, not the final model, and
      is expected to disappear once real memberships are backfilled.
    - Either way, the effective role is validated against the
      application's supported roles (see app.models.roles) and never
      silently treated as admin if unrecognized - an invalid role fails
      closed with 403.

    TEMPORARY ALUMNI COMPATIBILITY (see
    app.services.temporary_alumni_context_policy): an account whose
    effective GLOBAL role is "alumni" is additionally granted read-context
    access (never admin - see below) to `stars-national`/`fsu-stars` even
    when it HAS other UserOrganization rows that don't include them. This
    never applies to `organization`s outside those two slugs, and never
    upgrades the granted role above "alumni" - a request for any other
    organization the account isn't a member of is still a plain 403.
    """
    from app.config import get_settings

    slug = organization or get_settings().default_organization_slug
    organization_record = db.query(Organization).filter(Organization.slug == slug).first()
    if organization_record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    memberships = db.query(UserOrganization).filter(UserOrganization.user_id == current_user.id).all()

    if not memberships:
        effective_role = resolve_effective_role(None, current_user.role)
        if effective_role is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account role is not recognized")
        return OrganizationAccess(
            organization=organization_record, effective_role=effective_role, membership=None, is_legacy_access=True
        )

    matching_membership = next(
        (m for m in memberships if m.organization_id == organization_record.id), None
    )
    if matching_membership is None:
        effective_global_role = resolve_effective_role(None, current_user.role)
        if is_temporary_alumni_compatibility_slug(slug, effective_global_role):
            # Fails closed automatically: is_temporary_alumni_compatibility_slug
            # only ever matches effective_global_role == "alumni" - never
            # None (invalid role) and never "admin" - so this can never
            # grant more than plain alumni read-context access.
            return OrganizationAccess(
                organization=organization_record,
                effective_role=UserRole.ALUMNI.value,
                membership=None,
                is_legacy_access=False,
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this organization"
        )

    effective_role = resolve_effective_role(matching_membership.role, current_user.role)
    if effective_role is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Membership role is not recognized")

    return OrganizationAccess(
        organization=organization_record,
        effective_role=effective_role,
        membership=matching_membership,
        is_legacy_access=False,
    )


def require_admin_role(current_user: CurrentUser) -> None:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")


def require_admin_role_for(organization_access: OrganizationAccess) -> None:
    """Organization-scoped admin check (Phase 2): unlike require_admin_role
    above (which only ever looks at the account's global users.role), this
    checks the EFFECTIVE role already resolved for the specific
    organization in `organization_access` (see get_authorized_organization) -
    i.e. a per-organization membership role if one exists, otherwise the
    legacy-fallback global role. Always fails closed: effective_role has
    already been validated against SUPPORTED_USER_ROLES by
    get_authorized_organization, so this is a plain equality check, never
    a guess."""
    if organization_access.effective_role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required for this organization",
        )


def require_alumni_profile(current_user: CurrentUser) -> str:
    """Returns the current user's linked alumni_id, or 404 if this account
    has no associated alumni profile."""
    if not current_user.alumni_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No alumni profile is linked to this account",
        )
    return current_user.alumni_id
