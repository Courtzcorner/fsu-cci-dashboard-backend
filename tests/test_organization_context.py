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


def test_legacy_admin_sees_institution_context_with_no_active_dataset(db_session, admin_user, organization):
    """Documented Phase 1 legacy-fallback behavior: an admin with zero
    memberships sees every organization, including an institution context
    that has no active dataset yet, so they can prepare to import into it."""
    current_user = _current_user(admin_user)
    contexts = build_available_contexts(db_session, current_user)

    slugs = {c.slug for c in contexts}
    assert organization.slug in slugs
    ctx = next(c for c in contexts if c.slug == organization.slug)
    assert ctx.has_active_dataset is False
    assert ctx.can_import is True


def test_legacy_alumni_does_not_see_institution_context_with_no_active_dataset(
    db_session, alumni_user, organization, other_organization
):
    # `alumni_user` (via the `organization` fixture) is itself linked to
    # `organization` as an active alumni record, so use a second,
    # unrelated institution organization to test the "no active dataset
    # at all" case in isolation.
    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)

    assert all(c.slug != other_organization.slug for c in contexts)


def test_legacy_alumni_sees_institution_context_once_it_has_an_active_dataset(db_session, alumni_user, organization):
    _link_active_alumni(db_session, organization)
    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)

    ctx = next((c for c in contexts if c.slug == organization.slug), None)
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


def test_membership_based_alumni_sees_only_matching_organizations(db_session, alumni_user, organization):
    national = _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    _link_active_alumni(db_session, organization)
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=organization.id, role="alumni"))
    db_session.commit()

    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)

    slugs = {c.slug for c in contexts}
    assert slugs == {organization.slug}
    assert national.slug not in slugs


def test_national_and_fsu_membership_alumni_sees_both(db_session, alumni_user, organization):
    national = _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    _link_active_alumni(db_session, organization)
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=organization.id, role="alumni"))
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=national.id, role="alumni"))
    db_session.commit()

    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)

    assert {c.slug for c in contexts} == {organization.slug, national.slug}


def test_admin_membership_sets_can_import_true(db_session, admin_user, organization):
    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=organization.id, role="admin"))
    db_session.commit()

    current_user = _current_user(admin_user)
    contexts = build_available_contexts(db_session, current_user)
    ctx = next(c for c in contexts if c.slug == organization.slug)
    assert ctx.can_import is True
    assert ctx.role == "admin"


def test_alumni_membership_sets_can_import_false(db_session, alumni_user, organization):
    _link_active_alumni(db_session, organization)
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=organization.id, role="alumni"))
    db_session.commit()

    current_user = _current_user(alumni_user)
    contexts = build_available_contexts(db_session, current_user)
    ctx = next(c for c in contexts if c.slug == organization.slug)
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


def test_endpoint_response_contains_no_internal_database_ids(client, admin_user, organization):
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


def test_endpoint_legacy_admin_response_shape(client, admin_user, organization):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.get("/organizations/available-contexts", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    contexts = {c["slug"]: c for c in response.json()["contexts"]}
    assert contexts[organization.slug]["role"] == "admin"
    assert contexts[organization.slug]["can_import"] is True


def test_endpoint_legacy_alumni_response_shape(client, alumni_user, organization, other_organization):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get("/organizations/available-contexts", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    contexts = {c["slug"]: c for c in response.json()["contexts"]}
    # This alumni's own alumni record is actively linked to `organization`
    # (see conftest._make_alumni_with_user), so that context IS visible...
    assert organization.slug in contexts
    assert contexts[organization.slug]["role"] == "alumni"
    assert contexts[organization.slug]["can_import"] is False
    # ...but an unrelated institution context with no active dataset at
    # all is hidden from an alumni account (legacy fallback rule) - see
    # module docstring.
    assert other_organization.slug not in contexts


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
