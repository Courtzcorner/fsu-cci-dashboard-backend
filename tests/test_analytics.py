from app.models.alumni import Alumni, AlumniOrganization


def _add_alumni(db_session, organization, **overrides):
    defaults = dict(
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        verified=True,
        verification_status="verified",
        profile_completion=80,
        location_normalization_status="normalized",
    )
    defaults.update(overrides)
    alumni = Alumni(**defaults)
    db_session.add(alumni)
    db_session.flush()
    db_session.add(AlumniOrganization(alumni_id=alumni.id, organization_id=organization.id))
    db_session.commit()
    return alumni


def test_analytics_groups_cities_by_city_and_state_not_merged(client, db_session, organization):
    _add_alumni(db_session, organization, full_name="A B", city="Brooklyn", state="New York", metro_area="New York City Metropolitan Area")
    _add_alumni(db_session, organization, full_name="C D", city="New York City", state="New York", metro_area="New York City Metropolitan Area")
    _add_alumni(db_session, organization, full_name="E F", city="Brooklyn", state="New York", metro_area="New York City Metropolitan Area")

    token = client.post("/login", json={"username": "admin", "password": "AdminPass123!"}).json()["access_token"]
    response = client.get(
        "/analytics/summary", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()

    cities = {(c["city"], c["state"]): c["count"] for c in body["top_cities"]}
    assert cities[("Brooklyn", "New York")] == 2
    assert cities[("New York City", "New York")] == 1


def test_analytics_groups_metro_areas_together(client, db_session, organization):
    _add_alumni(db_session, organization, full_name="A B", city="Brooklyn", state="New York", metro_area="New York City Metropolitan Area")
    _add_alumni(db_session, organization, full_name="C D", city="New York City", state="New York", metro_area="New York City Metropolitan Area")

    token = client.post("/login", json={"username": "admin", "password": "AdminPass123!"}).json()["access_token"]
    response = client.get(
        "/analytics/summary", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {token}"}
    )
    body = response.json()
    metro_counts = {m["name"]: m["count"] for m in body["top_metro_areas"]}
    assert metro_counts["New York City Metropolitan Area"] == 2


def test_analytics_verification_percentage(client, db_session, organization):
    _add_alumni(db_session, organization, full_name="A B", verified=True)
    _add_alumni(db_session, organization, full_name="C D", verified=False, verification_status="unverified")

    token = client.post("/login", json={"username": "admin", "password": "AdminPass123!"}).json()["access_token"]
    response = client.get(
        "/analytics/summary", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {token}"}
    )
    body = response.json()
    assert body["total_alumni"] == 2
    assert body["verified_alumni"] == 1
    assert body["verification_percentage"] == 50.0
