"""
Phase 2: organization-aware admin endpoints.

Covers POST /admin/import-alumni, GET /admin/current-import, and
GET /admin/export-alumni now resolving their target organization through
`?organization=<slug>` (falling back to DEFAULT_ORGANIZATION_SLUG when
omitted) via app.deps.get_authorized_organization, and requiring an
EFFECTIVE per-organization admin role via require_admin_role_for - not
just the account's global users.role.

Cross-organization isolation is the primary thing under test here:
importing into one organization must never create, activate, deactivate,
or export data belonging to a different organization, and current-import
must never report another organization's history.

NOTE on Alumni.is_active (see app/routers/admin_routes.py docstring):
Alumni.is_active is global to an Alumni row, not scoped to an
AlumniOrganization link. Isolation between organizations tested here
holds because import-created Alumni rows are linked to exactly one
organization - nothing in this phase links one Alumni row to more than
one organization.
"""
import csv
import io

import pytest

from app.models.alumni import Alumni, AlumniOrganization
from app.models.organization import Organization
from app.models.user import User
from app.models.user_organization import UserOrganization
from app.security import hash_password
from app.services.temporary_alumni_context_policy import TEMPORARY_ALUMNI_COMPATIBLE_SLUGS
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, ALUMNI_PASSWORD, ALUMNI_USERNAME, login
from tests.test_import import _upload


def _csv_of(rows: list[tuple[str, str]]) -> str:
    lines = ["First Name,Last Name,LinkedIn URL"]
    for i, (first, last) in enumerate(rows):
        lines.append(f"{first},{last},linkedin.com/in/{first.lower()}{last.lower()}{i}")
    return "\n".join(lines) + "\n"


def _make_organization(db_session, slug, name, context_type="institution"):
    org = Organization(name=name, slug=slug, context_type=context_type)
    db_session.add(org)
    db_session.commit()
    return org


def _make_admin_with_membership(db_session, username, organization, role="admin"):
    """A NON-legacy admin: has exactly one explicit UserOrganization
    membership, so get_authorized_organization restricts them to exactly
    that organization (see app.deps.get_authorized_organization)."""
    user = User(username=username, password_hash=hash_password(ADMIN_PASSWORD), role="admin")
    db_session.add(user)
    db_session.flush()
    db_session.add(UserOrganization(user_id=user.id, organization_id=organization.id, role=role))
    db_session.commit()
    return user


def _active_alumni_count(db_session, organization) -> int:
    return (
        db_session.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id, Alumni.is_active.is_(True))
        .count()
    )


@pytest.fixture()
def fsu_stars(db_session):
    return _make_organization(db_session, "fsu-stars", "FSU STARS")


# --------------------------------------------------------------------------
# 1-4: import/replace-mode isolation between organizations
# --------------------------------------------------------------------------


def test_import_into_fsu_stars_creates_active_rows_only_in_fsu_stars(
    client, organization, fsu_stars, admin_user, db_session
):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = _upload(client, token, _csv_of([("Fiona", "Stars")]), organization="fsu-stars")
    assert response.status_code == 200
    assert response.json()["organization"] == "fsu-stars"
    assert response.json()["created"] == 1

    assert _active_alumni_count(db_session, fsu_stars) == 1
    assert _active_alumni_count(db_session, organization) == 0


def test_import_into_fsu_stars_does_not_deactivate_fsu_cci_alumni(
    client, organization, fsu_stars, admin_user, db_session
):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, _csv_of([("Carol", "Cci")]), organization="fsu-cci")
    assert _active_alumni_count(db_session, organization) == 1

    _upload(client, token, _csv_of([("Fiona", "Stars")]), organization="fsu-stars")

    assert _active_alumni_count(db_session, organization) == 1
    fsu_cci_alumni = db_session.query(Alumni).filter(Alumni.first_name == "Carol").one()
    assert fsu_cci_alumni.is_active is True


def test_import_into_fsu_cci_does_not_affect_fsu_stars(client, organization, fsu_stars, admin_user, db_session):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, _csv_of([("Fiona", "Stars")]), organization="fsu-stars")
    assert _active_alumni_count(db_session, fsu_stars) == 1

    _upload(client, token, _csv_of([("Carol", "Cci")]), organization="fsu-cci")

    assert _active_alumni_count(db_session, fsu_stars) == 1
    fsu_stars_alumni = db_session.query(Alumni).filter(Alumni.first_name == "Fiona").one()
    assert fsu_stars_alumni.is_active is True


def test_replace_mode_is_local_to_requested_organization(client, organization, fsu_stars, admin_user, db_session):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, _csv_of([("Carol", "Cci")]), organization="fsu-cci")
    _upload(client, token, _csv_of([("A", "Stars"), ("B", "Stars"), ("C", "Stars")]), organization="fsu-stars")
    assert _active_alumni_count(db_session, fsu_stars) == 3
    assert _active_alumni_count(db_session, organization) == 1

    # A smaller re-upload into fsu-stars replaces ONLY fsu-stars's dataset.
    response = _upload(client, token, _csv_of([("A", "Stars")]), organization="fsu-stars")
    assert response.status_code == 200
    assert response.json()["active_database_total"] == 1

    assert _active_alumni_count(db_session, fsu_stars) == 1
    assert _active_alumni_count(db_session, organization) == 1  # fsu-cci untouched


# --------------------------------------------------------------------------
# 5-6: current-import isolation
# --------------------------------------------------------------------------


def test_current_import_returns_selected_organization_only(client, organization, fsu_stars, admin_user):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, _csv_of([("Carol", "Cci")]), organization="fsu-cci")
    _upload(client, token, _csv_of([("A", "Stars"), ("B", "Stars")]), organization="fsu-stars")

    fsu_cci_status = client.get(
        "/admin/current-import", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {token}"}
    ).json()
    fsu_stars_status = client.get(
        "/admin/current-import", params={"organization": "fsu-stars"}, headers={"Authorization": f"Bearer {token}"}
    ).json()

    assert fsu_cci_status["active_database_total"] == 1
    assert fsu_cci_status["organization"] == "fsu-cci"
    assert fsu_stars_status["active_database_total"] == 2
    assert fsu_stars_status["organization"] == "fsu-stars"
    assert fsu_cci_status["csv_import_id"] != fsu_stars_status["csv_import_id"]


def test_current_import_for_org_with_no_imports_does_not_leak_another_organizations_import(
    client, organization, fsu_stars, admin_user
):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    # Only fsu-cci ever receives an import; fsu-stars never does.
    _upload(client, token, _csv_of([("Carol", "Cci")]), organization="fsu-cci")

    response = client.get(
        "/admin/current-import", params={"organization": "fsu-stars"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "none"
    assert body["active_database_total"] == 0
    assert body["csv_import_id"] is None
    assert body["organization"] == "fsu-stars"


# --------------------------------------------------------------------------
# 7: export isolation
# --------------------------------------------------------------------------


def test_export_contains_only_selected_organizations_active_alumni(client, organization, fsu_stars, admin_user):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, _csv_of([("Carol", "Cci")]), organization="fsu-cci")
    _upload(client, token, _csv_of([("Fiona", "Stars")]), organization="fsu-stars")

    export = client.get(
        "/admin/export-alumni", params={"organization": "fsu-stars"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert export.status_code == 200
    rows = list(csv.DictReader(io.StringIO(export.text)))
    assert len(rows) == 1
    assert rows[0]["First Name"] == "Fiona"
    assert all(row["First Name"] != "Carol" for row in rows)


# --------------------------------------------------------------------------
# 8-10: explicit per-organization membership authorization
# --------------------------------------------------------------------------


def test_fsu_only_admin_can_operate_on_fsu(client, fsu_stars, db_session):
    fsu_admin = _make_admin_with_membership(db_session, "fsu_admin", fsu_stars, role="admin")
    token = login(client, "fsu_admin", ADMIN_PASSWORD)

    import_response = _upload(client, token, _csv_of([("Fiona", "Stars")]), organization="fsu-stars")
    assert import_response.status_code == 200

    current_import = client.get(
        "/admin/current-import", params={"organization": "fsu-stars"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert current_import.status_code == 200

    export = client.get(
        "/admin/export-alumni", params={"organization": "fsu-stars"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert export.status_code == 200
    _ = fsu_admin


def test_fsu_only_admin_receives_403_for_national(client, fsu_stars, db_session):
    national = _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    _make_admin_with_membership(db_session, "fsu_admin", fsu_stars, role="admin")
    token = login(client, "fsu_admin", ADMIN_PASSWORD)

    for response in (
        _upload(client, token, _csv_of([("X", "Y")]), organization="stars-national"),
        client.get(
            "/admin/current-import",
            params={"organization": "stars-national"},
            headers={"Authorization": f"Bearer {token}"},
        ),
        client.get(
            "/admin/export-alumni",
            params={"organization": "stars-national"},
            headers={"Authorization": f"Bearer {token}"},
        ),
    ):
        assert response.status_code == 403
    _ = national


def test_national_only_admin_receives_403_for_fsu(client, fsu_stars, db_session):
    national = _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    _make_admin_with_membership(db_session, "national_admin", national, role="admin")
    token = login(client, "national_admin", ADMIN_PASSWORD)

    for response in (
        _upload(client, token, _csv_of([("X", "Y")]), organization="fsu-stars"),
        client.get(
            "/admin/current-import", params={"organization": "fsu-stars"}, headers={"Authorization": f"Bearer {token}"}
        ),
        client.get(
            "/admin/export-alumni", params={"organization": "fsu-stars"}, headers={"Authorization": f"Bearer {token}"}
        ),
    ):
        assert response.status_code == 403


def test_alumni_only_membership_receives_403_on_its_own_organization(client, fsu_stars, db_session):
    """An explicit membership with role="alumni" for fsu-stars must still
    be denied admin actions on fsu-stars itself - membership alone is not
    enough, the EFFECTIVE role must be admin."""
    _make_admin_with_membership(db_session, "fsu_alumni_role", fsu_stars, role="alumni")
    token = login(client, "fsu_alumni_role", ADMIN_PASSWORD)

    response = client.get(
        "/admin/current-import", params={"organization": "fsu-stars"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403


def test_invalid_membership_role_fails_closed_and_never_grants_import(client, organization, db_session, admin_user):
    db_session.add(UserOrganization(user_id=admin_user.id, organization_id=organization.id, role="superuser"))
    db_session.commit()
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    response = _upload(client, token, _csv_of([("X", "Y")]), organization="fsu-cci")
    assert response.status_code == 403


# --------------------------------------------------------------------------
# 11: legacy admin retains default behavior
# --------------------------------------------------------------------------


def test_legacy_admin_with_no_membership_retains_default_behavior(client, organization, admin_user, db_session):
    assert db_session.query(UserOrganization).filter(UserOrganization.user_id == admin_user.id).count() == 0
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    response = _upload(client, token, _csv_of([("Legacy", "Admin")]))
    assert response.status_code == 200
    assert response.json()["organization"] == organization.slug  # DEFAULT_ORGANIZATION_SLUG

    current_import = client.get("/admin/current-import", headers={"Authorization": f"Bearer {token}"})
    assert current_import.status_code == 200
    assert current_import.json()["organization"] == organization.slug

    export = client.get("/admin/export-alumni", headers={"Authorization": f"Bearer {token}"})
    assert export.status_code == 200


# --------------------------------------------------------------------------
# 12: alumni role is denied on all three endpoints
# --------------------------------------------------------------------------


def test_alumni_role_receives_403_for_all_three_admin_endpoints(client, organization, admin_user, alumni_user):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)

    assert _upload(client, token, _csv_of([("X", "Y")])).status_code == 403
    assert client.get("/admin/current-import", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert client.get("/admin/export-alumni", headers={"Authorization": f"Bearer {token}"}).status_code == 403


# --------------------------------------------------------------------------
# 13: unknown organization slug
# --------------------------------------------------------------------------


def test_unknown_organization_returns_404_for_all_three_endpoints(client, organization, admin_user):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    assert _upload(client, token, _csv_of([("X", "Y")]), organization="does-not-exist").status_code == 404
    assert (
        client.get(
            "/admin/current-import",
            params={"organization": "does-not-exist"},
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/admin/export-alumni",
            params={"organization": "does-not-exist"},
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 404
    )


# --------------------------------------------------------------------------
# 14: omitting organization preserves exact default behavior
# --------------------------------------------------------------------------


def test_omitting_organization_preserves_exact_default_behavior(client, organization, admin_user, db_session):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    with_default = _upload(client, token, _csv_of([("Default", "Org")]))
    with_explicit_slug = client.post(
        "/admin/import-alumni",
        params={"organization": organization.slug},
        files={"file": ("alumni.csv", io.BytesIO(_csv_of([("Default", "Org")]).encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert with_default.status_code == with_explicit_slug.status_code == 200
    assert with_default.json()["organization"] == with_explicit_slug.json()["organization"] == organization.slug
    assert with_default.json()["active_database_total"] == with_explicit_slug.json()["active_database_total"] == 1


# --------------------------------------------------------------------------
# 15: failed FSU import rollback isolation
# --------------------------------------------------------------------------


def test_failed_fsu_import_preserves_previous_fsu_and_fsu_cci_datasets(
    client, organization, fsu_stars, admin_user, db_session
):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, _csv_of([("Carol", "Cci")]), organization="fsu-cci")
    _upload(client, token, _csv_of([("Fiona", "Stars")]), organization="fsu-stars")
    assert _active_alumni_count(db_session, organization) == 1
    assert _active_alumni_count(db_session, fsu_stars) == 1

    # A CSV with one data row that fails required-field validation (blank
    # First Name/Last Name) - rows_parsed > 0 but csv_rows_valid == 0 -
    # triggers the zero-valid-rows rollback safety net in
    # import_alumni_csv (a genuinely empty/header-only file is NOT this
    # case - see that function's own safety-net comment - so this test
    # deliberately uses an invalid row, not an empty file).
    bad_csv = "First Name,Last Name,LinkedIn URL\n,,\n"
    response = client.post(
        "/admin/import-alumni",
        params={"organization": "fsu-stars"},
        files={"file": ("bad.csv", io.BytesIO(bad_csv.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 500

    assert _active_alumni_count(db_session, fsu_stars) == 1
    assert _active_alumni_count(db_session, organization) == 1
    fsu_stars_alumni = db_session.query(Alumni).filter(Alumni.first_name == "Fiona").one()
    assert fsu_stars_alumni.is_active is True


# --------------------------------------------------------------------------
# Temporary alumni compatibility policy must not leak into admin
# authorization - see app.services.temporary_alumni_context_policy. An
# effective-role-alumni account must remain 403 on all three admin
# endpoints for BOTH stars-national and fsu-stars, exactly as for any
# other organization.
# --------------------------------------------------------------------------


def test_alumni_remains_403_on_all_three_admin_endpoints_for_both_temporary_contexts(
    client, organization, admin_user, alumni_user, db_session
):
    for slug in TEMPORARY_ALUMNI_COMPATIBLE_SLUGS:
        _make_organization(db_session, slug, slug.replace("-", " ").title(), context_type="institution")

    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)

    for slug in TEMPORARY_ALUMNI_COMPATIBLE_SLUGS:
        assert _upload(client, token, _csv_of([("X", "Y")]), organization=slug).status_code == 403
        assert (
            client.get(
                "/admin/current-import", params={"organization": slug}, headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/admin/export-alumni", params={"organization": slug}, headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 403
        )


# --------------------------------------------------------------------------
# 17: no internal organization IDs exposed
# --------------------------------------------------------------------------


def test_responses_contain_no_internal_organization_ids(client, organization, admin_user):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    import_body = _upload(client, token, _csv_of([("No", "Ids")])).json()
    assert import_body["organization"] == organization.slug
    assert "organization_id" not in import_body
    assert import_body["organization"] != organization.id

    current_import_body = client.get(
        "/admin/current-import", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert current_import_body["organization"] == organization.slug
    assert "organization_id" not in current_import_body
