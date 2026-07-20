import io

from app.models.alumni import Alumni


def _login(client, username, password):
    return client.post("/login", json={"username": username, "password": password}).json()["access_token"]


def _upload(client, token, organization_slug, csv_text):
    return client.post(
        "/admin/import-alumni",
        data={"organization": organization_slug},
        files={"file": ("alumni.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )


CSV_BASIC = """First Name,Last Name,Graduation Year,Location,LinkedIn,Job Title,Company
Jordan,Lee,2022,"Brooklyn, NY",linkedin.com/in/jordanlee,Product Manager,Capital One
Maria,Gomez,2019,"Tallahassee, FL",,Reporter,WCTV
"""


def test_csv_import_creates_records(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, "fsu-cci", CSV_BASIC)
    assert response.status_code == 200

    body = response.json()
    assert body["created"] == 2
    assert body["updated"] == 0
    assert body["failed"] == 0

    records = db_session.query(Alumni).all()
    assert len(records) == 2
    brooklyn = next(r for r in records if r.first_name == "Jordan")
    assert brooklyn.location_original == "Brooklyn, NY"
    assert brooklyn.city == "Brooklyn"
    assert brooklyn.state == "New York"
    assert brooklyn.linkedin_url == "https://linkedin.com/in/jordanlee"


def test_csv_import_prevents_duplicates_via_linkedin_url(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, "fsu-cci", CSV_BASIC)

    updated_csv = CSV_BASIC.replace("Product Manager", "Senior Product Manager")
    response = _upload(client, token, "fsu-cci", updated_csv)

    body = response.json()
    assert body["created"] == 0
    assert body["updated"] == 2

    records = db_session.query(Alumni).all()
    assert len(records) == 2
    jordan = next(r for r in records if r.first_name == "Jordan")
    assert jordan.job_title == "Senior Product Manager"


def test_csv_import_does_not_merge_on_name_alone(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    csv_text = (
        "First Name,Last Name,Graduation Year,Location\n"
        "Jordan,Lee,2022,\"Brooklyn, NY\"\n"
    )
    _upload(client, token, "fsu-cci", csv_text)

    different_grad_year_csv = (
        "First Name,Last Name,Graduation Year,Location\n"
        "Jordan,Lee,2010,\"Atlanta, GA\"\n"
    )
    response = _upload(client, token, "fsu-cci", different_grad_year_csv)
    body = response.json()
    assert body["created"] == 1

    records = db_session.query(Alumni).filter(Alumni.first_name == "Jordan").all()
    assert len(records) == 2


def test_csv_import_reports_row_errors_for_missing_required_fields(client, organization, admin_user):
    token = _login(client, "admin", "AdminPass123!")
    csv_text = "First Name,Last Name\n,\nJordan,Lee\n"
    response = _upload(client, token, "fsu-cci", csv_text)
    body = response.json()
    assert body["failed"] == 1
    assert body["created"] == 1
    assert len(body["row_errors"]) == 1


def test_csv_import_requires_admin_role(client, organization, alumni_user):
    token = _login(client, "jdoe", "AlumniPass123!")
    response = _upload(client, token, "fsu-cci", CSV_BASIC)
    assert response.status_code == 403


def test_csv_import_defaults_to_fsu_cci_when_organization_field_is_omitted(client, organization, admin_user):
    """organization: str = Form(default="fsu-cci") - omitting the form
    field entirely must still import against fsu-cci, not fail/400."""
    token = _login(client, "admin", "AdminPass123!")
    response = client.post(
        "/admin/import-alumni",
        # No "organization" key in the form data at all.
        files={"file": ("alumni.csv", io.BytesIO(CSV_BASIC.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["organization"] == "fsu-cci"
    assert response.json()["created"] == 2


def test_csv_import_uses_submitted_organization_when_provided(client, admin_user, db_session):
    from app.models.organization import Organization

    other_org = Organization(name="STARS National", slug="stars-national")
    db_session.add(other_org)
    db_session.commit()

    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, "stars-national", CSV_BASIC)
    assert response.status_code == 200
    assert response.json()["organization"] == "stars-national"


def test_csv_import_rejects_unknown_organization_even_with_valid_admin(client, admin_user):
    """The submitted form field alone never grants access - an admin
    cannot import into an organization that doesn't exist in the database."""
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, "does-not-exist", CSV_BASIC)
    assert response.status_code == 404
