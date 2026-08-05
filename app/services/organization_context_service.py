"""Phase 1 multi-institution infrastructure: computing which dashboard
contexts (organizations) a user may see/switch into.

Nothing in this module is wired into any existing endpoint yet
(GET /alumni-data, GET /analytics/*, CSV import, etc. are all untouched) -
it only backs the new GET /organizations/available-contexts endpoint (see
app.routers.organization_routes) and the new authorization dependency
(see app.deps.get_authorized_organization).

LEGACY FALLBACK (temporary rollout compatibility, not the final model):
A user with zero UserOrganization rows has not yet been migrated to
explicit per-organization membership, so - to avoid breaking any existing
account before a deliberate backfill runs - they are shown every
organization exactly as if they were a member of all of them, using their
existing global `users.role` as the effective role everywhere. This
matches today's actual behavior (any authenticated user can already pass
`?organization=<any slug>` to every existing organization-aware
endpoint). Once real UserOrganization rows exist for an account, that
account is restricted to exactly its memberships and this fallback no
longer applies to it.
"""
from sqlalchemy.orm import Session

from app.deps import CurrentUser
from app.models.alumni import Alumni, AlumniOrganization
from app.models.organization import Organization
from app.models.roles import UserRole, resolve_effective_role
from app.models.user_organization import UserOrganization
from app.schemas.organization import AvailableContextOut


def organization_has_active_dataset(db: Session, organization_id: str) -> bool:
    """True only when at least one Alumni row is currently active AND
    linked to this organization via AlumniOrganization - i.e. whether a
    user switching into this context would actually see data today.

    Deliberately NOT based on CSVImport history: an organization that has
    a past (possibly since-fully-deactivated, or otherwise historical)
    CSVImport row but no currently active linked alumni must report
    has_active_dataset=False. CSVImport rows remain the right source for
    separate import-history/status reporting, just not for this flag.
    """
    exists_query = (
        db.query(AlumniOrganization.id)
        .join(Alumni, Alumni.id == AlumniOrganization.alumni_id)
        .filter(AlumniOrganization.organization_id == organization_id, Alumni.is_active.is_(True))
        .exists()
    )
    return bool(db.query(exists_query).scalar())


def _candidate_organizations_with_roles(
    db: Session, current_user: CurrentUser
) -> list[tuple[Organization, str | None]]:
    """Returns (organization, raw_per_org_role) pairs: the organizations
    to consider for this user, before dataset/role filtering.

    - If the user has any explicit memberships, only those organizations
      are candidates (raw_per_org_role = that membership's role, which
      may be None to mean "inherit users.role").
    - Otherwise (legacy account), every organization is a candidate, with
      no per-org role override (raw_per_org_role = None), so the
      fallback global role is used for every one of them - see the
      module docstring.
    """
    memberships = db.query(UserOrganization).filter(UserOrganization.user_id == current_user.id).all()
    if memberships:
        return [(membership.organization, membership.role) for membership in memberships]
    return [(organization, None) for organization in db.query(Organization).all()]


def build_available_contexts(db: Session, current_user: CurrentUser) -> list[AvailableContextOut]:
    """Builds the full, filtered list of contexts for GET
    /organizations/available-contexts. Read-only: issues only SELECT
    queries and never touches the user/session.
    """
    results: list[AvailableContextOut] = []

    for organization, raw_role in _candidate_organizations_with_roles(db, current_user):
        effective_role = resolve_effective_role(raw_role, current_user.role)
        if effective_role is None:
            # Fail closed: an unrecognized role (per-org override or the
            # account's own global role) never grants visibility into a
            # context, and is never treated as admin.
            continue

        is_admin_here = effective_role == UserRole.ADMIN.value
        has_active_dataset = organization_has_active_dataset(db, organization.id)

        if organization.context_type == "institution" and not is_admin_here and not has_active_dataset:
            # An institution context with no active data yet is hidden
            # from non-admins; an admin still sees it so they can import
            # into it. National contexts are never hidden by this rule.
            continue

        results.append(
            AvailableContextOut(
                slug=organization.slug,
                display_name=organization.name,
                context_type=organization.context_type,
                role=effective_role,
                has_active_dataset=has_active_dataset,
                can_import=is_admin_here,
                theme_key=organization.theme_key,
            )
        )

    return results
