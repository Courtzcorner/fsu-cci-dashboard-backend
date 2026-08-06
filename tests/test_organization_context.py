"""
Phase 1 multi-institution infrastructure: organization context metadata,
UserOrganization membership, the app.deps.get_authorized_organization
dependency, and GET /organizations/available-contexts.

Nothing here exercises any existing organization-aware endpoint
(GET /alumni-data, GET /analytics/*, CSV import, etc.) - those are
untouched in this phase and are covered by their own existing test files.
"""
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.deps import CurrentUser, get_authorized_organization
from app.models.alumni import Alumni, AlumniOrganization
from app.models.audit import CSVImport
from app.models.organization import Organization
from app.models.user_organization import UserOrganization
from app.services.organization_context_service import (
    build_available_contexts,
    organization_has_active_dataset,
)
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, ALUMNI_PASSWORD, ALUMNI_USERNAME, login


def _make_organization(db_session, slug, name, context_type="institution", theme_key=None):
    org = Organization(name=name, slug=slug, context_type=context_type, theme_key=theme_key)
    db_session.add(org)
    db_session.commit()
    return org


def _current_user(user, role=None):
    return CurrentUser(id=user.id, username=user.username, role=role or user.role, alumni_id=user.alumni_id)


def _link_active_alumni(db_session, organization):
    alumni = Alumni(first_name="Active", last_name="Person", full_name="Active Person", is_active=True)
    db_session.add(alumni)
    db_session.flush()
    db_session.add(AlumniOrganization(alumni_id=alumni.id, organization_id=organization.id))
    db_session.commit()
    return alumni


# --------------------------------------------------------------------------
# organization_has_active_dataset
# --------------------------------------------------------------------------


def test_has_active_dataset_false_with_no_data(db_session, organization):
    assert organization_has_active_dataset(db_session, organization.id) is False


def test_has_active_dataset_true_with_active_linked_alumni(db_session, organization):
    _link_active_alumni(db_session, organization)
    assert organization_has_active_dataset(db_session, organization.id) is True


def test_has_active_dataset_false_when_linked_alumni_is_inactive(db_session, organization):
    alumni = Alumni(first_name="Inactive", last_name="Person", full_name="Inactive Person", is_active=False)
    db_session.add(alumni)
    db_session.flush()
    db_session.add(AlumniOrganization(alumni_id=alumni.id, organization_id=organization.id))
    db_session.commit()

    assert organization_has_active_dataset(db_session, organization.id) is False


def test_has_active_dataset_false_with_only_a_historical_csv_import_row(db_session, organization):
    """An old CSVImport row with no currently active linked alumni must
    NOT count as an active dataset - has_active_dataset is defined purely
    in terms of currently-active AlumniOrganization-linked alumni."""
    db_session.add(CSVImport(organization_id=organization.id, filename="old_import.csv", rows_received=10))
    db_session.commit()

    assert organization_has_active_dataset(db_session, organization.id) is False


# --------------------------------------------------------------------------
# UserOrganization uniqueness
# --------------------------------------------------------------------------


def test_duplicate_membership_is_rejected(db_session, admin_user, organization):
    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=organization.id, role="admin"))
    db_session.commit()

    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=organization.id, role="admin"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# --------------------------------------------------------------------------
# get_authorized_organization
# --------------------------------------------------------------------------


def test_fsu_only_membership_cannot_access_national(db_session, admin_user):
    national = _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    fsu = _make_organization(db_session, "fsu-stars", "FSU STARS", context_type="institution")
    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=fsu.id, role="admin"))
    db_session.commit()

    current_user = _current_user(admin_user)

    access = get_authorized_organization(organization="fsu-stars", current_user=current_user, db=db_session)
    assert access.organization.slug == "fsu-stars"
    assert access.effective_role == "admin"
    assert access.is_legacy_access is False

    with pytest.raises(HTTPException) as exc_info:
        get_authorized_organization(organization="stars-national", current_user=current_user, db=db_session)
    assert exc_info.value.status_code == 403
    _ = national  # exists so the slug resolves to a real (but unauthorized) organization


def test_national_and_fsu_membership_can_access_both(db_session, admin_user):
    national = _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    fsu = _make_organization(db_session, "fsu-stars", "FSU STARS", context_type="institution")
    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=national.id, role="admin"))
    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=fsu.id, role="admin"))
    db_session.commit()

    current_user = _current_user(admin_user)
    for slug in ("stars-national", "fsu-stars"):
        access = get_authorized_organization(organization=slug, current_user=current_user, db=db_session)
        assert access.organization.slug == slug
        assert access.is_legacy_access is False


def test_legacy_user_with_no_memberships_can_access_any_existing_organization(db_session, admin_user, organization):
    other = _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    current_user = _current_user(admin_user)

    for slug in (organization.slug, other.slug):
        access = get_authorized_organization(organization=slug, current_user=current_user, db=db_session)
        assert access.is_legacy_access is True
        assert access.effective_role == "admin"


def test_invalid_membership_role_fails_closed_and_never_grants_import(db_session, admin_user, organization):
    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=organization.id, role="superuser"))
    db_session.commit()

    current_user = _current_user(admin_user)
    with pytest.raises(HTTPException) as exc_info:
        get_authorized_organization(organization=organization.slug, current_user=current_user, db=db_session)
    assert exc_info.value.status_code == 403


def test_nonexistent_organization_slug_returns_404(db_session, admin_user):
    current_user = _current_user(admin_user)
    with pytest.raises(HTTPException) as exc_info:
        get_authorized_organization(organization="does-not-exist", current_user=current_user, db=db_session)
    assert exc_info.value.status_code == 404


# --------------------------------------------------------------------------
# build_available_contexts / GET /organizations/available-contexts
# --------------------------------------------------------------------------


def test_legacy_admin_sees_institution_context_with_no_active_dataset(db_session, admin_user):
    """Documented Phase 1 legacy-fallback behavior: an admin with zero
    memberships sees every (non-hidden) organization, including an
    institution context that has no active dataset yet, so they can
    prepare to import into it. Deliberately NOT the `organization` fixture
    (slug "fsu-cci") - that slug is now unconditionally hidden (see
    app.services.hidden_context_policy), so a separate institution is
    needed to isolate this legacy-fallback behavior."""
    other = _make_organization(db_session, "unrelated-institution", "Unrelated Institution")
    current_user = _current_user(admin_user)
    contexts = build_available_contexts(db_session, current_user)

    slugs = {c.slug for c in contexts}
    assert other.slug in slugs
    ctx = next(c for c in contexts if c.slug == other.slug)
    assert ctx.has_active_dataset is False
    assert ctx.can_import is True


def test_legacy_alumni_does_not_see_institution_context_with_no_active_dataset(
    db_session, alumni_user, organization
):
    # `alumni_user` (via the `organization` fixture) is itself linked to
    # `organization` as an active alumni record, so use a second,
    # unrelated institution organization to test the "no active dataset
    # at all" case in isolation. Deliberately NOT `other_organization`
    # (slug "stars-national") - that slug is now always visible under the
    # temporary alumni compatibility policy (see
    # app.services.temporary_alumni_context_policy), so it would no
    # longer isolate this case.
    unrelated = _make_organization(db_session, "unrelated-institution", "Unrelated Institution")
    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)

    assert all(c.slug != unrelated.slug for c in contexts)


def test_legacy_alumni_sees_institution_context_once_it_has_an_active_dataset(db_session, alumni_user):
    # Deliberately NOT the `organization` fixture (slug "fsu-cci") - see
    # test_legacy_admin_sees_institution_context_with_no_active_dataset.
    other = _make_organization(db_session, "unrelated-institution", "Unrelated Institution")
    _link_active_alumni(db_session, other)
    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)

    ctx = next((c for c in contexts if c.slug == other.slug), None)
    assert ctx is not None
    assert ctx.has_active_dataset is True
    assert ctx.can_import is False


def test_national_context_remains_visible_for_legacy_alumni_with_no_active_dataset(db_session, alumni_user):
    national = _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)

    ctx = next((c for c in contexts if c.slug == national.slug), None)
    assert ctx is not None
    assert ctx.has_active_dataset is False


def test_membership_based_alumni_sees_only_matching_organizations(db_session, alumni_user):
    # Deliberately NOT the `organization` fixture (slug "fsu-cci", now
    # unconditionally hidden - see app.services.hidden_context_policy)
    # and NOT "stars-national"/"fsu-stars" - those two slugs are now
    # always visible to an alumni account under the temporary
    # compatibility policy (see
    # app.services.temporary_alumni_context_policy), so freshly-created,
    # otherwise-unrelated slugs are needed to isolate "membership
    # restricts candidates to exactly its memberships" in this test.
    member_org = _make_organization(db_session, "member-institution", "Member Institution")
    unrelated = _make_organization(db_session, "unrelated-institution", "Unrelated Institution")
    _link_active_alumni(db_session, member_org)
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=member_org.id, role="alumni"))
    db_session.commit()

    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)

    slugs = {c.slug for c in contexts}
    assert slugs == {member_org.slug}
    assert unrelated.slug not in slugs


def test_national_and_fsu_membership_alumni_sees_both(db_session, alumni_user):
    institution = _make_organization(db_session, "member-institution", "Member Institution")
    national = _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    _link_active_alumni(db_session, institution)
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=institution.id, role="alumni"))
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=national.id, role="alumni"))
    db_session.commit()

    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)

    assert {c.slug for c in contexts} == {institution.slug, national.slug}


def test_admin_membership_sets_can_import_true(db_session, admin_user):
    institution = _make_organization(db_session, "member-institution", "Member Institution")
    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=institution.id, role="admin"))
    db_session.commit()

    current_user = _current_user(admin_user)
    contexts = build_available_contexts(db_session, current_user)
    ctx = next(c for c in contexts if c.slug == institution.slug)
    assert ctx.can_import is True
    assert ctx.role == "admin"


def test_alumni_membership_sets_can_import_false(db_session, alumni_user):
    institution = _make_organization(db_session, "member-institution", "Member Institution")
    _link_active_alumni(db_session, institution)
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=institution.id, role="alumni"))
    db_session.commit()

    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)
    ctx = next(c for c in contexts if c.slug == institution.slug)
    assert ctx.can_import is False
    assert ctx.role == "alumni"


def test_invalid_membership_role_is_excluded_from_available_contexts(db_session, admin_user, organization):
    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=organization.id, role="superuser"))
    db_session.commit()

    current_user = _current_user(admin_user)
    contexts = build_available_contexts(db_session, current_user)
    assert all(c.slug != organization.slug for c in contexts)


# --------------------------------------------------------------------------
# GET /organizations/available-contexts (HTTP layer)
# --------------------------------------------------------------------------


def test_endpoint_response_contains_no_internal_database_ids(client, admin_user, organization, db_session):
    # `organization` (fsu-cci) is now unconditionally hidden - see
    # app.services.hidden_context_policy - so a second, visible
    # institution is added to guarantee at least one context is returned.
    _make_organization(db_session, "member-institution", "Member Institution")
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.get("/organizations/available-contexts", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert "contexts" in body
    assert len(body["contexts"]) >= 1
    for context in body["contexts"]:
        assert set(context.keys()) == {
            "slug", "display_name", "context_type", "role", "has_active_dataset", "can_import", "theme_key",
        }


def test_endpoint_legacy_admin_response_shape(client, admin_user, organization, db_session):
    # Deliberately NOT the `organization` fixture (slug "fsu-cci", now
    # unconditionally hidden - see app.services.hidden_context_policy).
    institution = _make_organization(db_session, "member-institution", "Member Institution")
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.get("/organizations/available-contexts", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    contexts = {c["slug"]: c for c in response.json()["contexts"]}
    assert contexts[institution.slug]["role"] == "admin"
    assert contexts[institution.slug]["can_import"] is True


def test_endpoint_legacy_alumni_response_shape(client, alumni_user, organization, db_session):
    # Deliberately freshly created orgs, NOT the `organization` fixture
    # (slug "fsu-cci", now unconditionally hidden - see
    # app.services.hidden_context_policy) and NOT `other_organization`
    # (slug "stars-national") - that slug is now always visible to an
    # alumni account under the temporary compatibility policy (see
    # app.services.temporary_alumni_context_policy).
    institution = _make_organization(db_session, "member-institution", "Member Institution")
    _link_active_alumni(db_session, institution)
    unrelated = _make_organization(db_session, "unrelated-institution", "Unrelated Institution")
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get("/organizations/available-contexts", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    contexts = {c["slug"]: c for c in response.json()["contexts"]}
    # `institution` has an active dataset, so it IS visible...
    assert institution.slug in contexts
    assert contexts[institution.slug]["role"] == "alumni"
    assert contexts[institution.slug]["can_import"] is False
    # ...but an unrelated institution context with no active dataset at
    # all is hidden from an alumni account (legacy fallback rule) - see
    # module docstring.
    assert unrelated.slug not in contexts


# --------------------------------------------------------------------------
# TEMPORARY alumni compatibility policy (stars-national / fsu-stars) - see
# app.services.temporary_alumni_context_policy. Remove this whole section
# (and the policy module + its two call sites) once explicit per-alumni
# institution assignment ships.
# --------------------------------------------------------------------------


def test_legacy_alumni_sees_both_temporary_contexts(db_session, alumni_user):
    _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    _make_organization(db_session, "fsu-stars", "FSU STARS", context_type="institution")

    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)

    slugs = {c.slug for c in contexts}
    assert "stars-national" in slugs
    assert "fsu-stars" in slugs


def test_alumni_with_unrelated_explicit_membership_still_sees_both_temporary_contexts(db_session, alumni_user):
    # Deliberately NOT the `organization` fixture (slug "fsu-cci", now
    # unconditionally hidden - see app.services.hidden_context_policy) as
    # the "real membership" org.
    institution = _make_organization(db_session, "member-institution", "Member Institution")
    national = _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    fsu = _make_organization(db_session, "fsu-stars", "FSU STARS", context_type="institution")
    _link_active_alumni(db_session, institution)
    # The alumni's only explicit membership is to an unrelated org.
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=institution.id, role="alumni"))
    db_session.commit()

    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)

    slugs = {c.slug for c in contexts}
    assert national.slug in slugs
    assert fsu.slug in slugs
    assert institution.slug in slugs  # the real membership is preserved too


def test_temporary_contexts_appear_with_has_active_dataset_false_when_empty(db_session, alumni_user):
    _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    _make_organization(db_session, "fsu-stars", "FSU STARS", context_type="institution")

    current_user = _current_user(alumni_user)
    contexts = {c.slug: c for c in build_available_contexts(db_session, current_user)}

    assert contexts["stars-national"].has_active_dataset is False
    assert contexts["fsu-stars"].has_active_dataset is False
    # And they are still returned, not hidden, despite having no data.
    assert "stars-national" in contexts
    assert "fsu-stars" in contexts


def test_temporary_fsu_stars_context_becomes_active_once_linked_alumni_exist(db_session, alumni_user):
    fsu = _make_organization(db_session, "fsu-stars", "FSU STARS", context_type="institution")
    current_user = _current_user(alumni_user)

    before = {c.slug: c for c in build_available_contexts(db_session, current_user)}
    assert before["fsu-stars"].has_active_dataset is False

    _link_active_alumni(db_session, fsu)

    after = {c.slug: c for c in build_available_contexts(db_session, current_user)}
    assert after["fsu-stars"].has_active_dataset is True


def test_temporary_contexts_have_alumni_role_and_cannot_import(db_session, alumni_user):
    _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    _make_organization(db_session, "fsu-stars", "FSU STARS", context_type="institution")

    current_user = _current_user(alumni_user)
    contexts = {c.slug: c for c in build_available_contexts(db_session, current_user)}

    for slug in ("stars-national", "fsu-stars"):
        assert contexts[slug].role == "alumni"
        assert contexts[slug].can_import is False


def test_temporary_policy_does_not_grant_access_to_an_arbitrary_third_institution(
    db_session, alumni_user, organization
):
    _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    _make_organization(db_session, "fsu-stars", "FSU STARS", context_type="institution")
    other = _make_organization(db_session, "some-other-institution", "Some Other Institution")
    # An explicit (even if unrelated) membership is required here so this
    # account is NOT on the zero-membership legacy-fallback path (which
    # already grants unrestricted access to every organization, for
    # unrelated reasons - see the module docstring) - this isolates the
    # temporary policy's own boundary specifically.
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=organization.id, role="alumni"))
    db_session.commit()

    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)

    assert all(c.slug != other.slug for c in contexts)

    with pytest.raises(HTTPException) as exc_info:
        get_authorized_organization(organization=other.slug, current_user=current_user, db=db_session)
    assert exc_info.value.status_code == 403


def test_temporary_policy_authorizes_read_context_for_stars_national_and_fsu_stars(db_session, alumni_user):
    """get_authorized_organization (used directly by admin routes, and
    potentially future alumni-facing endpoints) grants read-context
    access under the temporary rule - always at effective_role="alumni",
    never elevated - even when the account's only real membership is
    unrelated."""
    _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    _make_organization(db_session, "fsu-stars", "FSU STARS", context_type="institution")
    unrelated = _make_organization(db_session, "unrelated-org", "Unrelated Org")
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=unrelated.id, role="alumni"))
    db_session.commit()

    current_user = _current_user(alumni_user)
    for slug in ("stars-national", "fsu-stars"):
        access = get_authorized_organization(organization=slug, current_user=current_user, db=db_session)
        assert access.organization.slug == slug
        assert access.effective_role == "alumni"


def test_temporary_policy_never_applies_to_admin_effective_global_role(db_session, admin_user, organization):
    """An admin account with an unrelated explicit membership must NOT
    get the temporary alumni carve-out - it should still be a plain 403,
    exactly as before this policy existed."""
    _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=organization.id, role="admin"))
    db_session.commit()

    current_user = _current_user(admin_user)
    with pytest.raises(HTTPException) as exc_info:
        get_authorized_organization(organization="stars-national", current_user=current_user, db=db_session)
    assert exc_info.value.status_code == 403


def test_available_contexts_are_returned_in_deterministic_order(db_session, alumni_user):
    """National first, then alphabetically by display name - regardless
    of frozenset/query iteration order, and regardless of which contexts
    came from the temporary policy vs. a real membership/legacy
    fallback."""
    _make_organization(db_session, "fsu-stars", "FSU STARS", context_type="institution")
    _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    aaa = _make_organization(db_session, "aaa-institution", "AAA Institution", context_type="institution")
    _link_active_alumni(db_session, aaa)

    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)
    slugs_in_order = [c.slug for c in contexts]

    national_indices = [i for i, c in enumerate(contexts) if c.context_type == "national"]
    non_national_indices = [i for i, c in enumerate(contexts) if c.context_type != "national"]
    assert not national_indices or max(national_indices) < min(non_national_indices)

    non_national_display_names = [c.display_name for c in contexts if c.context_type != "national"]
    assert non_national_display_names == sorted(non_national_display_names)
    _ = slugs_in_order


def test_temporary_contexts_expose_no_internal_organization_ids(client, alumni_user):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get("/organizations/available-contexts", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    for context in response.json()["contexts"]:
        assert set(context.keys()) == {
            "slug", "display_name", "context_type", "role", "has_active_dataset", "can_import", "theme_key",
        }


# --------------------------------------------------------------------------
# HIDDEN CONTEXT POLICY (fsu-cci) - see app.services.hidden_context_policy.
# Remove this whole section (and the policy module + its two call sites)
# once fsu-cci is formally retired or repurposed for a new institution.
# --------------------------------------------------------------------------


def test_fsu_cci_absent_for_legacy_admin(db_session, admin_user, organization):
    """`organization` (see tests/conftest.py) IS fsu-cci - a legacy admin
    with zero memberships would otherwise see it via the legacy fallback,
    including with an active dataset (added here so admin-visibility
    rules alone could never explain its absence), but the hidden-context
    policy must exclude it unconditionally."""
    _link_active_alumni(db_session, organization)
    current_user = _current_user(admin_user)
    contexts = build_available_contexts(db_session, current_user)
    assert all(c.slug != "fsu-cci" for c in contexts)


def test_fsu_cci_absent_for_legacy_alumni(db_session, alumni_user, organization):
    # alumni_user's own alumni record is actively linked to `organization`
    # (fsu-cci) - see conftest._make_alumni_with_user - so this also
    # confirms the hidden-context check runs BEFORE the active-dataset
    # visibility rule, not after.
    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)
    assert all(c.slug != "fsu-cci" for c in contexts)


def test_fsu_cci_absent_with_explicit_membership(db_session, alumni_user, organization):
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=organization.id, role="alumni"))
    db_session.commit()

    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)
    assert all(c.slug != "fsu-cci" for c in contexts)


def test_fsu_cci_absent_with_explicit_admin_membership(db_session, admin_user, organization):
    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=organization.id, role="admin"))
    db_session.commit()

    current_user = _current_user(admin_user)
    contexts = build_available_contexts(db_session, current_user)
    assert all(c.slug != "fsu-cci" for c in contexts)


def test_stars_national_and_fsu_stars_remain_available_alongside_hidden_fsu_cci(db_session, alumni_user, organization):
    _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    _make_organization(db_session, "fsu-stars", "FSU STARS", context_type="institution")

    current_user = _current_user(alumni_user)
    slugs = {c.slug for c in build_available_contexts(db_session, current_user)}

    assert "stars-national" in slugs
    assert "fsu-stars" in slugs
    assert "fsu-cci" not in slugs


def test_alumni_direct_fsu_cci_read_returns_403(client, alumni_user, organization):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get(
        "/alumni-data", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
    # Generic, non-revealing message - never confirms/denies data exists.
    assert response.json()["detail"] == "Access denied"


def test_alumni_direct_fsu_cci_analytics_read_returns_403(client, alumni_user, organization):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get(
        "/analytics/summary", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403


def test_alumni_direct_access_to_stars_national_and_fsu_stars_unchanged(db_session, client, alumni_user):
    _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    _make_organization(db_session, "fsu-stars", "FSU STARS", context_type="institution")
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)

    for slug in ("stars-national", "fsu-stars"):
        response = client.get(
            "/alumni-data", params={"organization": slug}, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200


def test_admin_direct_fsu_cci_read_remains_allowed_for_maintenance(client, admin_user, organization):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.get(
        "/alumni-data", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200


def test_admin_maintenance_endpoints_can_still_target_fsu_cci(client, admin_user, organization):
    """The three organization-scoped admin endpoints (which use
    get_authorized_organization / require_admin_role_for, a completely
    separate dependency from get_organization_by_slug_for_current_user)
    must remain unaffected by the hidden-context policy."""
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    current_import = client.get(
        "/admin/current-import", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert current_import.status_code == 200

    export = client.get(
        "/admin/export-alumni", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert export.status_code == 200


def test_fsu_cci_data_and_relationship_counts_unchanged_by_hiding_it(db_session, alumni_user, organization):
    """Hiding fsu-cci from the selector/reads must never touch its
    underlying data - confirm the Organization row, its Alumni link, and
    the alumni record itself are all fully intact."""
    alumni_count_before = (
        db_session.query(AlumniOrganization).filter(AlumniOrganization.organization_id == organization.id).count()
    )
    assert alumni_count_before >= 1

    current_user = _current_user(alumni_user)
    build_available_contexts(db_session, current_user)  # exercise the hiding path

    db_session.refresh(organization)
    assert organization.slug == "fsu-cci"
    alumni_count_after = (
        db_session.query(AlumniOrganization).filter(AlumniOrganization.organization_id == organization.id).count()
    )
    assert alumni_count_after == alumni_count_before


def test_endpoint_is_read_only_and_does_not_alter_user_or_session_state(
    client, db_session, admin_user, organization
):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    token_version_before = admin_user.token_version
    membership_count_before = db_session.query(UserOrganization).count()

    response = client.get("/organizations/available-contexts", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    db_session.refresh(admin_user)
    assert admin_user.token_version == token_version_before
    assert db_session.query(UserOrganization).count() == membership_count_before
    # The same (unmodified) token still works for a second call.
    second_response = client.get("/organizations/available-contexts", headers={"Authorization": f"Bearer {token}"})
    assert second_response.status_code == 200


# --------------------------------------------------------------------------
# Alembic chain
# --------------------------------------------------------------------------


def test_alembic_has_a_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    repo_root = Path(__file__).resolve().parent.parent
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "alembic"))
    script_dir = ScriptDirectory.from_config(config)

    heads = script_dir.get_heads()
    assert len(heads) == 1
    assert heads[0] == "90745d7d8acb"
