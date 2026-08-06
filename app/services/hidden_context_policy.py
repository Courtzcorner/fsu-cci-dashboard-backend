"""TEMPORARY ROLLOUT COMPATIBILITY POLICY - remove once fsu-cci is either
formally retired or repurposed for a new institution.

fsu-cci holds real, active legacy data and would otherwise remain visible
in GET /organizations/available-contexts forever - an institution context
WITH an active dataset is never hidden by the normal "no active dataset"
rule (see organization_has_active_dataset /
build_available_contexts in app.services.organization_context_service).
Product wants it fully absent from the context selector for every user -
admin, alumni, legacy, or explicit-membership - without touching its row,
its data, or treating it as a National substitute.

This is intentionally narrow (exactly one slug today) and centralized in
this one module, with its two call sites, so it can be deleted in a
single place once fsu-cci is repurposed or retired for real:
- app.services.organization_context_service.build_available_contexts
  (removes it from the context selector, for every role)
- app.deps.get_organization_by_slug_for_current_user
  (blocks direct ?organization=fsu-cci reads for non-admin callers on the
  normal alumni-facing read endpoints - /alumni-data, /analytics/*,
  /content/* - with a generic, non-revealing 403; admin endpoints, which
  use get_authorized_organization/require_admin_role_for instead, are
  NOT affected - see app.deps for that dependency)

This module NEVER:
- deletes, renames, or otherwise modifies the Organization row
- touches Alumni, AlumniOrganization, CSVImport, or any other data
- treats fsu-cci as a substitute for stars-national or any other slug
- affects admin authorization (require_admin_role_for is untouched and
  keeps working for fsu-cci exactly as before, for maintenance access)
"""
HIDDEN_ORGANIZATION_SLUGS = frozenset({"fsu-cci"})


def is_hidden_organization_slug(slug: str) -> bool:
    return slug in HIDDEN_ORGANIZATION_SLUGS
