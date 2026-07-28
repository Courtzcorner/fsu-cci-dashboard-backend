"""
Tests for the additive shared content-synchronization system:
app.models.content_version, app.services.content_version_service, and
GET /sync/status - plus the version bumps wired into CSV import, event/
speaker/super-star mutations, and profile-link moderation/updates.

Additive-only: none of these tests touch or depend on changing
authentication, JWT behavior, temporary-account setup, role
authorization, CSV parsing/replacement-mode rules, profile matching
rules, or existing analytics calculations - they only prove the new
version-tracking layer works correctly on top of them.
"""
from app.models.content_version import ContentVersion
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, ALUMNI_PASSWORD, ALUMNI_USERNAME, login
from tests.test_import import CSV_BASIC, _upload
from tests.test_profile_linking import _auth, _put_my_profile, _register_alumni_user, _upload_csv


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _sync_status(client, token):
    response = client.get("/sync/status", headers=_auth_header(token))
    assert response.status_code == 200, response.text
    return response.json()


def _versions(db_session) -> dict:
    return {row.domain: row.version for row in db_session.query(ContentVersion).all()}


# --------------------------------------------------------------------------
# 1-2. CSV import
# --------------------------------------------------------------------------


def test_successful_csv_import_increments_alumni_analytics_locations_universities_and_global(
    client, organization, admin_user, db_session
):
    before = _versions(db_session)
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    response = _upload(client, token, CSV_BASIC)
    assert response.status_code == 200, response.text

    db_session.expire_all()
    after = _versions(db_session)
    for domain in ("alumni", "analytics", "locations", "universities", "global"):
        assert after.get(domain, 0) == before.get(domain, 0) + 1, f"{domain} did not increment: {before} -> {after}"


def test_failed_csv_import_does_not_increment_versions(client, organization, admin_user, db_session):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    before = _versions(db_session)

    # Zero valid rows (every row missing required first/last name) - the
    # import pipeline rejects this and rolls back, per existing behavior.
    bad_csv = "First Name,Last Name\n,\n"
    response = _upload(client, token, bad_csv)
    assert response.status_code == 500

    db_session.expire_all()
    after = _versions(db_session)
    assert after == before


# --------------------------------------------------------------------------
# 3-5. Events
# --------------------------------------------------------------------------


def test_creating_an_event_increments_events_and_global(client, admin_user, organization, db_session):
    before = _versions(db_session)
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.post(
        "/admin/events",
        params={"organization": "fsu-cci"},
        json={"title": "Homecoming", "start_date": "2026-10-01T18:00:00Z", "is_published": True},
        headers=_auth_header(token),
    )
    assert response.status_code == 201, response.text

    db_session.expire_all()
    after = _versions(db_session)
    assert after.get("events", 0) == before.get("events", 0) + 1
    assert after.get("global", 0) == before.get("global", 0) + 1


def test_editing_an_event_increments_events_and_global(client, admin_user, organization, db_session):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    create_response = client.post(
        "/admin/events",
        params={"organization": "fsu-cci"},
        json={"title": "Original", "start_date": "2026-10-01T18:00:00Z", "is_published": True},
        headers=_auth_header(token),
    )
    event_id = create_response.json()["id"]

    db_session.expire_all()
    before = _versions(db_session)
    response = client.patch(
        f"/admin/events/{event_id}",
        params={"organization": "fsu-cci"},
        json={"title": "Updated"},
        headers=_auth_header(token),
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    after = _versions(db_session)
    assert after.get("events", 0) == before.get("events", 0) + 1
    assert after.get("global", 0) == before.get("global", 0) + 1


def test_deleting_an_event_increments_events_and_global(client, admin_user, organization, db_session):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    create_response = client.post(
        "/admin/events",
        params={"organization": "fsu-cci"},
        json={"title": "To Delete", "start_date": "2026-10-01T18:00:00Z", "is_published": True},
        headers=_auth_header(token),
    )
    event_id = create_response.json()["id"]

    db_session.expire_all()
    before = _versions(db_session)
    response = client.delete(
        f"/admin/events/{event_id}", params={"organization": "fsu-cci"}, headers=_auth_header(token)
    )
    assert response.status_code == 204

    db_session.expire_all()
    after = _versions(db_session)
    assert after.get("events", 0) == before.get("events", 0) + 1
    assert after.get("global", 0) == before.get("global", 0) + 1


# --------------------------------------------------------------------------
# 6. Super STARS
# --------------------------------------------------------------------------


def test_changing_a_super_star_increments_superstars_and_global(
    client, admin_user, alumni_user, alumni_record, organization, db_session
):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    before = _versions(db_session)

    create_response = client.post(
        "/admin/super-stars",
        params={"organization": "fsu-cci"},
        json={"alumni_id": alumni_record.id, "headline": "From intern to industry leader", "is_published": True},
        headers=_auth_header(token),
    )
    assert create_response.status_code == 201, create_response.text

    db_session.expire_all()
    after_create = _versions(db_session)
    assert after_create.get("superstars", 0) == before.get("superstars", 0) + 1
    assert after_create.get("global", 0) == before.get("global", 0) + 1

    super_star_id = create_response.json()["id"]
    before_feature = _versions(db_session)
    feature_response = client.patch(
        f"/admin/super-stars/{super_star_id}",
        params={"organization": "fsu-cci"},
        json={"featured_at": "2026-11-01"},
        headers=_auth_header(token),
    )
    assert feature_response.status_code == 200, feature_response.text

    db_session.expire_all()
    after_feature = _versions(db_session)
    assert after_feature.get("superstars", 0) == before_feature.get("superstars", 0) + 1
    assert after_feature.get("global", 0) == before_feature.get("global", 0) + 1


# --------------------------------------------------------------------------
# 7. Speakers
# --------------------------------------------------------------------------


def test_changing_a_speaker_increments_speakers_and_global(client, admin_user, organization, db_session):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    before = _versions(db_session)

    create_response = client.post(
        "/admin/speakers",
        params={"organization": "fsu-cci"},
        json={"name": "Dr. Casey Rivera", "job_title": "CTO", "company": "Acme Corp", "is_published": True},
        headers=_auth_header(token),
    )
    assert create_response.status_code == 201, create_response.text

    db_session.expire_all()
    after = _versions(db_session)
    assert after.get("speakers", 0) == before.get("speakers", 0) + 1
    assert after.get("global", 0) == before.get("global", 0) + 1


# --------------------------------------------------------------------------
# 8-9. Public linked profile updates
# --------------------------------------------------------------------------


def _link_profile(client, db_session, organization, admin_token, username, **privacy_fields):
    email = f"{username}@example.com"
    _upload_csv(
        client, admin_token,
        [f"Test,{username},{email},,Old Co,Old Title,Old City,GA,,Verified,2026-01-01,Old University"],
    )
    token = _register_alumni_user(client, db_session, organization, username=username)
    _put_my_profile(client, token, first_name=username, last_name="Test", primary_email=email)
    if privacy_fields:
        privacy_response = client.put("/profile/me/privacy", json=privacy_fields, headers=_auth(token))
        assert privacy_response.status_code == 200, privacy_response.text
    candidates = client.post("/profile/me/find-match", headers=_auth(token)).json()["candidates"]
    alumni_id = candidates[0]["alumni_id"]
    confirm_response = client.post(f"/profile/me/confirm-match/{alumni_id}", headers=_auth(token))
    assert confirm_response.status_code == 200, confirm_response.text
    return token, alumni_id


def test_linked_profile_company_update_increments_profiles_alumni_analytics_and_global(
    client, organization, admin_user, db_session
):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    token, _alumni_id = _link_profile(
        client, db_session, organization, admin_token, "companyupdater", show_current_employer=True
    )

    db_session.expire_all()
    before = _versions(db_session)
    response = client.put(
        "/profile/me", json={"current_employer": "New Employer Inc"}, headers=_auth(token)
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    after = _versions(db_session)
    assert after.get("profiles", 0) == before.get("profiles", 0) + 1
    assert after.get("alumni", 0) == before.get("alumni", 0) + 1
    assert after.get("analytics", 0) == before.get("analytics", 0) + 1
    assert after.get("global", 0) == before.get("global", 0) + 1
    # A pure company change never needs to bump the map/locations domain.
    assert after.get("locations", 0) == before.get("locations", 0)


def test_linked_profile_location_update_increments_profiles_alumni_locations_analytics_and_global(
    client, organization, admin_user, db_session
):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    token, _alumni_id = _link_profile(
        client, db_session, organization, admin_token, "locationupdater", show_location=True
    )

    db_session.expire_all()
    before = _versions(db_session)
    response = client.put(
        "/profile/me",
        json={"current_city": "Austin", "current_state": "Texas"},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    after = _versions(db_session)
    assert after.get("profiles", 0) == before.get("profiles", 0) + 1
    assert after.get("alumni", 0) == before.get("alumni", 0) + 1
    assert after.get("locations", 0) == before.get("locations", 0) + 1
    assert after.get("analytics", 0) == before.get("analytics", 0) + 1
    assert after.get("global", 0) == before.get("global", 0) + 1


def test_unlinked_profile_update_only_increments_profiles_and_global(
    client, organization, db_session
):
    token = _register_alumni_user(client, db_session, organization, username="unlinkedupdater")
    before = _versions(db_session)

    response = client.put(
        "/profile/me", json={"current_employer": "Somewhere Inc"}, headers=_auth(token)
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    after = _versions(db_session)
    assert after.get("profiles", 0) == before.get("profiles", 0) + 1
    assert after.get("global", 0) == before.get("global", 0) + 1
    # Never linked, so it cannot possibly affect the public directory,
    # analytics, or the map.
    assert after.get("alumni", 0) == before.get("alumni", 0)
    assert after.get("analytics", 0) == before.get("analytics", 0)
    assert after.get("locations", 0) == before.get("locations", 0)


# --------------------------------------------------------------------------
# 10-12. GET /sync/status
# --------------------------------------------------------------------------


def test_sync_status_is_accessible_to_admin_users(client, admin_user):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    body = _sync_status(client, token)
    assert "global_version" in body
    assert "domains" in body


def test_sync_status_is_accessible_to_alumni_users(client, alumni_user):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    body = _sync_status(client, token)
    assert "global_version" in body
    assert "domains" in body


def test_sync_status_requires_authentication(client):
    response = client.get("/sync/status")
    assert response.status_code in (401, 403)


def test_two_different_users_receive_the_same_shared_version_state(
    client, admin_user, alumni_user, organization, db_session
):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, admin_token, CSV_BASIC)

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)

    admin_status = _sync_status(client, admin_token)
    alumni_status = _sync_status(client, alumni_token)
    assert admin_status == alumni_status


def test_sync_status_contains_every_documented_domain(client, admin_user):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    body = _sync_status(client, token)
    for domain in (
        "alumni", "analytics", "locations", "events", "superstars", "speakers", "universities", "profiles",
    ):
        assert domain in body["domains"]


def test_sync_status_response_never_includes_alumni_or_analytics_payloads(client, admin_user):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    body = _sync_status(client, token)
    assert set(body.keys()) == {"global_version", "updated_at", "domains"}
    assert all(isinstance(v, int) for v in body["domains"].values())


def test_sync_status_response_has_no_store_cache_header(client, admin_user):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.get("/sync/status", headers=_auth_header(token))
    assert response.headers.get("cache-control") == "no-store"


def test_dynamic_data_endpoint_has_no_store_cache_header(client, admin_user, organization):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.get("/alumni-data", headers=_auth_header(token))
    assert response.headers.get("cache-control") == "no-store"


def test_static_uploads_are_not_marked_no_store(client):
    response = client.get("/uploads/does-not-exist.png")
    # The file itself doesn't exist (404), but the important thing is the
    # security-header middleware never force-adds a no-store header to
    # this static-asset path.
    assert response.headers.get("cache-control") != "no-store"


# --------------------------------------------------------------------------
# 13-14. Existing behavior unaffected (spot check - full suite run
# separately confirms this exhaustively)
# --------------------------------------------------------------------------


def test_login_still_works_and_is_unaffected_by_sync_system(client, admin_user):
    response = client.post("/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_csv_import_response_shape_is_unaffected(client, admin_user, organization):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = _upload(client, token, CSV_BASIC)
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 2
    assert body["failed"] == 0
