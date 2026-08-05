"""TEMPORARY ROLLOUT COMPATIBILITY POLICY.

Context: institution assignment for alumni accounts does not exist yet
(there is no UI/flow during alumni setup that links an account to a
specific institution). Until that ships, an alumni account that has no
UserOrganization memberships - or has memberships that simply don't
happen to include the two dashboards below - would otherwise see zero
(or an incomplete set of) available contexts from
GET /organizations/available-contexts, which the frontend surfaces as
"Your account is not authorized for any STARS data context yet."

Temporary rule: every account whose EFFECTIVE GLOBAL role is "alumni" is
unconditionally eligible to VIEW exactly these two contexts, regardless
of its UserOrganization rows (or lack thereof):

- stars-national
- fsu-stars

This is intentionally narrow and centralized in this one module so it can
be deleted in a single place, with its two call sites, once explicit
per-alumni institution assignment is implemented:
- app.services.organization_context_service.build_available_contexts
  (what is shown in the context switcher)
- app.deps.get_authorized_organization
  (defensive/forward-looking - so this policy also holds if an
  alumni-facing read endpoint is ever migrated onto this dependency;
  admin authorization is untouched - see require_admin_role_for, which
  independently requires effective_role == "admin" and is never
  satisfied by this policy)

This module NEVER:
- elevates role (the effective role granted here is always exactly
  "alumni", hardcoded, never inherited/upgraded to admin)
- grants access to any slug outside TEMPORARY_ALUMNI_COMPATIBLE_SLUGS
- treats fsu-cci as a substitute for stars-national
- creates, modifies, or reads UserOrganization rows
"""
from app.models.roles import UserRole

TEMPORARY_ALUMNI_COMPATIBLE_SLUGS = frozenset({"stars-national", "fsu-stars"})


def is_temporary_alumni_compatibility_slug(slug: str, effective_global_role: str | None) -> bool:
    """True only when `slug` is one of the two temporarily-compatible
    contexts AND the account's effective GLOBAL role (never a per-org
    override) is exactly "alumni". An unrecognized/invalid global role
    (effective_global_role not in SUPPORTED_USER_ROLES, e.g. already
    resolved to None by resolve_effective_role) never matches - this
    fails closed like every other role check in the app.
    """
    return slug in TEMPORARY_ALUMNI_COMPATIBLE_SLUGS and effective_global_role == UserRole.ALUMNI.value
