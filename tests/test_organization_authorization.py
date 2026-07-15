"""
Organization + role authorization behavior under CSV-based auth.

CSV users (admin/alumni) are not assigned to specific organizations, so
both roles may currently view any organization's dashboard - the
`organization` query param just selects which org's data to return, and
is validated against the database (404 if it doesn't exist). The `role`
claim instead gates admin-only *actions* (see tests/test_import.py).
"""
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, ALUMNI_PASSWORD, ALUMNI_USERNAME, login


def test_admin_can_view_any_organization_dashboard(client, organization, other_organization):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    for slug in ("fsu-cci", "stars-national"):
        response = client.get(
            "/alumni-data", params={"organization": slug}, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["meta"]["organization"] == slug


def test_alumni_role_can_also_view_the_dashboard(client, organization):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get(
        "/alumni-data", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200


def test_missing_organization_falls_back_to_default_slug(client, organization):
    token = login(client)
    response = client.get("/alumni-data", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["meta"]["organization"] == "fsu-cci"


def test_nonexistent_organization_returns_404(client, organization):
    token = login(client)
    response = client.get(
        "/alumni-data", params={"organization": "does-not-exist"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404


def test_alumni_data_is_still_scoped_per_organization(client, db_session, organization, other_organization):
    from app.models.alumni import Alumni, AlumniOrganization

    a = Alumni(first_name="A", last_name="One", full_name="A One")
    b = Alumni(first_name="B", last_name="Two", full_name="B Two")
    db_session.add_all([a, b])
    db_session.flush()
    db_session.add(AlumniOrganization(alumni_id=a.id, organization_id=organization.id))
    db_session.add(AlumniOrganization(alumni_id=b.id, organization_id=other_organization.id))
    db_session.commit()

    token = login(client)
    response = client.get(
        "/alumni-data", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {token}"}
    )
    names = [row["full_name"] for row in response.json()["data"]]
    assert names == ["A One"]
