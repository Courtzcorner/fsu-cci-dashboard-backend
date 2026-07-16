"""
Shared content endpoints (Events, Speakers, Super Stars) - all records
live in the shared database, admin writes are visible to alumni reads,
and admin-only write access is enforced.
"""
from app.database import SessionLocal, engine
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, ALUMNI_PASSWORD, ALUMNI_USERNAME, login


def test_admin_created_event_appears_in_get_events(client, admin_user, alumni_user, organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    create_response = client.post(
        "/admin/events",
        params={"organization": "fsu-cci"},
        json={
            "title": "Homecoming Mixer",
            "description": "Alumni networking event",
            "start_date": "2026-10-01T18:00:00Z",
            "location": "Tallahassee, FL",
            "is_published": True,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_response.status_code == 201
    event_id = create_response.json()["id"]

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    list_response = client.get(
        "/events", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {alumni_token}"}
    )
    assert list_response.status_code == 200
    titles = [e["title"] for e in list_response.json()]
    assert "Homecoming Mixer" in titles
    assert any(e["id"] == event_id for e in list_response.json())


def test_unpublished_event_is_hidden_from_get_events(client, admin_user, alumni_user, organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    client.post(
        "/admin/events",
        params={"organization": "fsu-cci"},
        json={"title": "Draft Event", "start_date": "2026-10-01T18:00:00Z", "is_published": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get(
        "/events", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {alumni_token}"}
    )
    assert "Draft Event" not in [e["title"] for e in response.json()]


def test_admin_created_speaker_appears_in_get_speakers(client, admin_user, alumni_user, organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    create_response = client.post(
        "/admin/speakers",
        params={"organization": "fsu-cci"},
        json={"name": "Dr. Casey Rivera", "job_title": "CTO", "company": "Acme Corp", "is_published": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_response.status_code == 201

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get(
        "/speakers", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {alumni_token}"}
    )
    assert response.status_code == 200
    names = [s["name"] for s in response.json()]
    assert "Dr. Casey Rivera" in names


def test_admin_created_super_star_appears_in_get_super_stars(client, admin_user, alumni_user, alumni_record, organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    create_response = client.post(
        "/admin/super-stars",
        params={"organization": "fsu-cci"},
        json={
            "alumni_id": alumni_record.id,
            "headline": "From intern to industry leader",
            "is_published": True,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_response.status_code == 201

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get(
        "/super-stars", params={"organization": "fsu-cci"}, headers={"Authorization": f"Bearer {alumni_token}"}
    )
    assert response.status_code == 200
    headlines = [s["headline"] for s in response.json()]
    assert "From intern to industry leader" in headlines


def test_alumni_role_cannot_create_event(client, alumni_user, organization):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.post(
        "/admin/events",
        params={"organization": "fsu-cci"},
        json={"title": "Should Fail", "start_date": "2026-10-01T18:00:00Z"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_alumni_role_cannot_create_speaker_or_super_star(client, alumni_user, alumni_record, organization):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)

    speaker_response = client.post(
        "/admin/speakers",
        params={"organization": "fsu-cci"},
        json={"name": "Should Fail"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert speaker_response.status_code == 403

    super_star_response = client.post(
        "/admin/super-stars",
        params={"organization": "fsu-cci"},
        json={"alumni_id": alumni_record.id, "headline": "Should Fail"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert super_star_response.status_code == 403


def test_alumni_role_cannot_update_or_delete_events(client, admin_user, alumni_user, organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    create_response = client.post(
        "/admin/events",
        params={"organization": "fsu-cci"},
        json={"title": "Original", "start_date": "2026-10-01T18:00:00Z", "is_published": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = create_response.json()["id"]

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    patch_response = client.patch(
        f"/admin/events/{event_id}",
        params={"organization": "fsu-cci"},
        json={"title": "Hacked"},
        headers={"Authorization": f"Bearer {alumni_token}"},
    )
    assert patch_response.status_code == 403

    delete_response = client.delete(
        f"/admin/events/{event_id}",
        params={"organization": "fsu-cci"},
        headers={"Authorization": f"Bearer {alumni_token}"},
    )
    assert delete_response.status_code == 403


def test_changes_remain_available_after_server_restart(admin_user, organization):
    """Simulates a server restart: write data through one DB session/engine
    connection, then open a brand new session (without dropping tables) and
    confirm the data is still there - proving persistence isn't in-memory.
    """
    from datetime import datetime, timezone

    from app.models.content import Event

    write_session = SessionLocal()
    event = Event(
        organization_id=organization.id,
        title="Persistent Event",
        start_date=datetime(2026, 11, 1, tzinfo=timezone.utc),
        is_published=True,
        created_by_user_id=admin_user.id,
    )
    write_session.add(event)
    write_session.commit()
    event_id = event.id
    write_session.close()

    # Dispose the engine's connection pool to force a fresh connection,
    # simulating the app process restarting while the database persists.
    engine.dispose()

    fresh_session = SessionLocal()
    reloaded = fresh_session.get(Event, event_id)
    assert reloaded is not None
    assert reloaded.title == "Persistent Event"
    fresh_session.close()
