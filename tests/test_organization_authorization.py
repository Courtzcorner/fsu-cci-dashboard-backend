"""
Organization + role authorization behavior.

Both admin and alumni roles may currently view any organization's
published dashboard content - the `organization` query param just selects
which org's data to return, and is validated against the database (404 if
it doesn't exist). The `role` claim instead gates admin-only *actions*
(see tests/test_import.py, tests/test_content.py).

EXCEPTION (temporary - see app.services.hidden_context_policy): a
non-admin who explicitly passes `?organization=fsu-cci` is now denied
with a 403, since fsu-cci is a hidden legacy context for alumni. This
does NOT affect a request that omits `?organization=` entirely, even
though DEFAULT_ORGANIZATION_SLUG (today's single-tenant production
default) is itself `fsu-cci` - see
test_missing_organization_falls_back_to_default_slug_for_alumni_too below.
"""
from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    ALUMNI_PASSWORD,
    ALUMNI_USERNAME,
    login,
)


def test_admin_can_view_any_organization_dashboard(client, admin_user, organization, other_organization):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    for slug in ("fsu-cci", "stars-national"):
        response = client.get(
            "/alumni-data", params={"organization": slug}, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["meta"]["organization"] == slug


def test_alumni_role_can_also_view_the_dashboard(client, alumni_user, other_organization):
    # Deliberately `other_organization` (slug "stars-national"), NOT the
    # `organization` fixture (slug "fsu-cci") - explicit alumni access to
    # fsu-cci is now blocked, see
    # test_alumni_explicit_fsu_cci_request_is_now_blocked below.
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get(
        "/alumni-data", params={"organization": "stars-national"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200


def test_alumni_explicit_fsu_cci_request_is_now_blocked(client, alumni_user, organization):
    """Temporary hidden-context policy (see
    app.services.hidden_context_policy): a non-admin who explicitly asks
    for fsu-cci is denied, with a generic message that doesn't reveal
    whether the org/data exists."""
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get(
        "/alumni-data", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Access denied"


def test_missing_organization_falls_back_to_default_slug(client, admin_user, organization):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.get("/alumni-data", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["meta"]["organization"] == "fsu-cci"


def test_missing_organization_falls_back_to_default_slug_for_alumni_too(client, alumni_user, organization):
    """Critical backward-compatibility guarantee: DEFAULT_ORGANIZATION_SLUG
    is itself `fsu-cci` today, so every existing alumni caller that omits
    `?organization=` entirely (i.e. every caller before multi-org
    selection ships) must keep working unchanged - only an EXPLICIT
    `?organization=fsu-cci` is treated as "selecting" the hidden context
    and blocked (see test_alumni_explicit_fsu_cci_request_is_now_blocked)."""
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get("/alumni-data", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["meta"]["organization"] == "fsu-cci"


def test_nonexistent_organization_returns_404(client, admin_user, organization):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.get(
        "/alumni-data", params={"organization": "does-not-exist"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404


def test_alumni_data_is_still_scoped_per_organization(client, db_session, admin_user, organization, other_organization):
    from app.models.alumni import Alumni, AlumniOrganization

    a = Alumni(first_name="A", last_name="One", full_name="A One")
    b = Alumni(first_name="B", last_name="Two", full_name="B Two")
    db_session.add_all([a, b])
    db_session.flush()
    db_session.add(AlumniOrganization(alumni_id=a.id, organization_id=organization.id))
    db_session.add(AlumniOrganization(alumni_id=b.id, organization_id=other_organization.id))
    db_session.commit()

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.get(
        "/alumni-data", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {token}"}
    )
    names = [row["full_name"] for row in response.json()["data"]]
    assert names == ["A One"]
