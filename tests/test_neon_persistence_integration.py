"""
End-to-end verification that CSV imports persist to the real database
(the same engine/session factory GET /alumni-data uses - Postgres/Neon in
production, SQLite for this test run) rather than to memory, a temp file,
or only an import-history table.

Flow verified:
  1. Log in as admin.
  2. Import a CSV.
  3. Open a brand new SQLAlchemy session (simulating a totally separate
     request/process) and confirm the rows are there.
  4. Call GET /alumni-data as admin.
  5. Call GET /alumni-data as a different, newly-authenticated alumni user.
  6. Confirm both seed from the exact same shared rows.
  7. Confirm the data is still there after the original session is closed.
"""
import io

from app.database import SessionLocal, engine
from app.models.alumni import Alumni, AlumniOrganization
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, ALUMNI_PASSWORD, ALUMNI_USERNAME, login

CSV_TEXT = """First Name,Last Name,Graduation Year,Location,LinkedIn,Job Title,Company,Major,University
Jordan,Lee,2022,"Tallahassee, FL",linkedin.com/in/jordanlee,Product Manager,Capital One,Marketing,Florida State University
Maria,Gomez,2019,"Brooklyn, NY",,Reporter,WCTV,Journalism,Florida State University
Sam,Osei,2021,"Atlanta, GA",linkedin.com/in/samosei,Data Analyst,Delta,Statistics,Florida State University
"""


def _upload(client, token, organization_slug, csv_text):
    return client.post(
        "/admin/import-alumni",
        data={"organization": organization_slug},
        files={"file": ("alumni.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_full_import_and_shared_read_persistence_flow(client, admin_user, alumni_user, organization):
    # 1. Log in as admin.
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    # 2. Import a CSV.
    import_response = _upload(client, admin_token, "fsu-cci", CSV_TEXT)
    assert import_response.status_code == 200
    import_body = import_response.json()
    assert import_body["organization"] == "fsu-cci"
    assert import_body["created"] == 3
    assert import_body["updated"] == 0
    assert import_body["skipped"] == 0
    assert import_body["failed"] == 0
    # alumni_user fixture already has 1 record in fsu-cci, plus the 3 just imported.
    assert import_body["database_total"] == 4

    # 3. Open a brand new SQLAlchemy session (not the request-scoped one
    #    used by the endpoints) and confirm the rows are actually there.
    engine.dispose()
    fresh_session = SessionLocal()
    try:
        persisted_names = {
            row.full_name
            for row in fresh_session.query(Alumni)
            .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
            .filter(AlumniOrganization.organization_id == organization.id)
            .all()
        }
    finally:
        fresh_session.close()
    assert {"Jordan Lee", "Maria Gomez", "Sam Osei"}.issubset(persisted_names)

    # 4. Call GET /alumni-data as admin.
    admin_read = client.get(
        "/alumni-data",
        params={"organization": "fsu-cci", "page_size": 5000},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert admin_read.status_code == 200
    admin_body = admin_read.json()
    assert admin_body["meta"]["organization"] == "fsu-cci"
    assert admin_body["meta"]["total"] == 4
    admin_names = {row["full_name"] for row in admin_body["data"]}

    # 5. Call GET /alumni-data as a different, newly-authenticated alumni user.
    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    alumni_read = client.get(
        "/alumni-data",
        params={"organization": "fsu-cci", "page_size": 5000},
        headers={"Authorization": f"Bearer {alumni_token}"},
    )
    assert alumni_read.status_code == 200
    alumni_body = alumni_read.json()
    alumni_names = {row["full_name"] for row in alumni_body["data"]}

    # 6. Confirm both roles see the exact same shared rows - no per-user
    #    filtering, no separate admin/alumni copies of the dataset.
    assert admin_names == alumni_names
    assert {"Jordan Lee", "Maria Gomez", "Sam Osei"}.issubset(alumni_names)
    assert admin_body["meta"]["total"] == alumni_body["meta"]["total"] == 4

    # Every row returned must be usable by the frontend without additional
    # lookups (map, directory, companies/industries/universities/seniority
    # all read straight from these fields).
    jordan = next(row for row in alumni_body["data"] if row["full_name"] == "Jordan Lee")
    for required_field in (
        "id", "first_name", "last_name", "full_name", "graduation_year", "major",
        "university", "job_title", "company", "industry", "seniority", "city",
        "state", "display_location", "linkedin_url", "verified",
    ):
        assert required_field in jordan, f"missing frontend-required field '{required_field}'"

    # 7. Confirm the data persists even though the request-scoped sessions
    #    used above have all already been closed by their endpoints, and
    #    the standalone verification session from step 3 was also closed.
    engine.dispose()
    verification_session = SessionLocal()
    try:
        final_count = (
            verification_session.query(Alumni)
            .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
            .filter(AlumniOrganization.organization_id == organization.id)
            .count()
        )
        assert final_count == 4
    finally:
        verification_session.close()


def test_alumni_data_does_not_require_verified_or_other_optional_fields_to_be_set(client, admin_user, organization):
    """Rows with verified=False (the default) and no is_active/is_published
    concept on Alumni at all must still be returned - GET /alumni-data must
    never silently filter imported records out."""
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    csv_text = "First Name,Last Name\nUnverified,Alum\n"
    _upload(client, admin_token, "fsu-cci", csv_text)

    response = client.get(
        "/alumni-data",
        params={"organization": "fsu-cci", "page_size": 5000},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    names = {row["full_name"]: row for row in response.json()["data"]}
    assert "Unverified Alum" in names
    assert names["Unverified Alum"]["verified"] is False


def test_page_size_supports_up_to_5000(client, admin_user, organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.get(
        "/alumni-data",
        params={"organization": "fsu-cci", "page_size": 5000},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["meta"]["page_size"] == 5000

    too_large = client.get(
        "/alumni-data",
        params={"organization": "fsu-cci", "page_size": 5001},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert too_large.status_code == 422
