"""
Proves that CSV imports are durably persisted in the shared database - not
frontend state, server memory, or a temporary file - and stay visible
across new requests, new sessions, new logins, and different users.
"""
import io

from app.database import SessionLocal, engine
from app.models.alumni import Alumni, AlumniOrganization
from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    ALUMNI_PASSWORD,
    ALUMNI_USERNAME,
    login,
)

CSV_249_ROWS = "First Name,Last Name,Graduation Year,Location\n" + "".join(
    f"Alum{i},Test{i},2020,\"Tallahassee, FL\"\n" for i in range(249)
)


def _upload(client, token, organization_slug, csv_text, filename="alumni.csv"):
    return client.post(
        "/admin/import-alumni",
        data={"organization": organization_slug},
        files={"file": (filename, io.BytesIO(csv_text.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_1_admin_import_reports_database_total(client, admin_user, organization):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = _upload(client, token, "fsu-cci", CSV_249_ROWS)
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 249
    assert body["updated"] == 0
    assert body["skipped"] == 0
    assert body["failed"] == 0
    assert body["database_total"] == 249


def test_2_a_second_request_still_retrieves_the_imported_records(client, admin_user, organization):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, "fsu-cci", CSV_249_ROWS)

    second_response = client.get(
        "/alumni-data",
        params={"organization": "fsu-cci", "page_size": 200},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second_response.status_code == 200
    assert second_response.json()["meta"]["total"] == 249


def test_3_a_newly_authenticated_alumni_user_retrieves_the_records(client, admin_user, alumni_user, organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, admin_token, "fsu-cci", CSV_249_ROWS)

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get(
        "/alumni-data",
        params={"organization": "fsu-cci", "page_size": 200},
        headers={"Authorization": f"Bearer {alumni_token}"},
    )
    assert response.status_code == 200
    # +1 for the alumni_user fixture's own linked alumni record, which
    # also belongs to fsu-cci.
    assert response.json()["meta"]["total"] == 250


def test_4_records_still_exist_in_a_brand_new_database_session(client, admin_user, organization):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, "fsu-cci", CSV_249_ROWS)

    # Dispose the connection pool and open a completely fresh session, to
    # rule out any request-scoped/in-memory caching.
    engine.dispose()
    fresh_session = SessionLocal()
    try:
        count = (
            fresh_session.query(Alumni)
            .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
            .filter(AlumniOrganization.organization_id == organization.id)
            .count()
        )
        assert count == 249
    finally:
        fresh_session.close()


def test_5_admin_can_sign_out_and_sign_back_in_and_still_see_the_records(client, admin_user, organization):
    first_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, first_token, "fsu-cci", CSV_249_ROWS)

    # "Sign out" - the client simply discards the token; there is no
    # server-side session to invalidate, which is itself proof state
    # isn't held in a stateful in-memory/session store.
    del first_token

    second_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.get(
        "/alumni-data",
        params={"organization": "fsu-cci", "page_size": 200},
        headers={"Authorization": f"Bearer {second_token}"},
    )
    assert response.json()["meta"]["total"] == 249


def test_6_alumni_user_cannot_import_a_csv(client, alumni_user, organization):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = _upload(client, token, "fsu-cci", CSV_249_ROWS)
    assert response.status_code == 403


def test_7_alumni_user_can_edit_only_their_own_profile(client, alumni_user, other_alumni_user, organization, db_session):
    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.patch(
        "/me/profile",
        json={"job_title": "Staff Engineer"},
        headers={"Authorization": f"Bearer {alumni_token}"},
    )
    assert response.status_code == 200
    assert response.json()["job_title"] == "Staff Engineer"

    other_alumni = db_session.get(Alumni, other_alumni_user.alumni_id)
    assert other_alumni.job_title != "Staff Engineer"


def test_import_updates_existing_records_without_duplicating(client, admin_user, organization, db_session):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, "fsu-cci", CSV_249_ROWS)

    # Change a non-key field (location) - graduation year stays the same so
    # the dedup match (first/last name + graduation year + org) still hits.
    updated_csv = CSV_249_ROWS.replace("Tallahassee, FL", "Miami, FL")
    response = _upload(client, token, "fsu-cci", updated_csv)
    body = response.json()
    assert body["created"] == 0
    assert body["updated"] == 249
    assert body["database_total"] == 249

    total_rows = db_session.query(Alumni).count()
    assert total_rows == 249


def test_import_transaction_rolls_back_on_unexpected_failure(client, admin_user, organization, db_session, monkeypatch):
    """If something fails after some rows have been staged but before the
    final commit, nothing should be persisted - proving there's no partial,
    non-transactional write path."""
    from app.services import csv_import_service

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure while recording CSVImport")

    monkeypatch.setattr(csv_import_service, "CSVImport", _boom)

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = _upload(client, token, "fsu-cci", CSV_249_ROWS)
    assert response.status_code == 500

    assert db_session.query(Alumni).count() == 0
