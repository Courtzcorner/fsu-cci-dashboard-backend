"""
Alumni event-speaker request workflow: an alumnus requests to speak at a
published Event, an admin reviews the request (seeing only the
requester's PUBLIC profile - reusing the exact same privacy rules as
`GET /public-profiles/{alumni_id}`) and can select/undo-select that
alumnus as the event's speaker.

Deliberately uses `other_organization` (slug "stars-national") for the
round-trip tests, matching the existing convention in test_content.py -
`organization` (slug "fsu-cci") is now a hidden legacy context and
non-admin/explicit requests to it are blocked (see
app.services.hidden_context_policy); admin access to it is unaffected
and is used for the cross-organization isolation tests below.
"""
from datetime import datetime, timezone

from app.models.content import Event
from app.models.event_speaker_request import EventSpeakerRequest
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, ALUMNI_PASSWORD, ALUMNI_USERNAME, login
from tests.test_profile_linking import _confirmed_alumni_id, _register_alumni_user


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _create_published_event(client, admin_token, organization_slug="stars-national", title="Career Panel"):
    response = client.post(
        "/admin/events",
        params={"organization": organization_slug},
        json={"title": title, "start_date": "2026-10-01T18:00:00Z", "is_published": True},
        headers=_auth(admin_token),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --------------------------------------------------------------------------
# Submission (existing behavior must remain unchanged, plus the new flow)
# --------------------------------------------------------------------------


def test_alumni_can_submit_a_speaker_request(client, admin_user, alumni_user, other_organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_id = _create_published_event(client, admin_token)

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.post(
        f"/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={"message": "I'd love to speak about my career path."},
        headers=_auth(alumni_token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "requested"
    assert body["event_id"] == event_id
    assert body["message"] == "I'd love to speak about my career path."
    assert body["alumni_full_name"] == "Jordan Doe"


def test_duplicate_speaker_request_is_idempotent(client, admin_user, alumni_user, other_organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_id = _create_published_event(client, admin_token)

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    first = client.post(
        f"/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={"message": "First message"},
        headers=_auth(alumni_token),
    )
    second = client.post(
        f"/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={"message": "Second message"},
        headers=_auth(alumni_token),
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    # The second call never overwrites the original request.
    assert second.json()["message"] == "First message"


def test_speaker_request_submission_unaffected_by_new_admin_features(client, admin_user, alumni_user, other_organization):
    """Regression: submitting a request still works exactly as introduced,
    independent of whether any admin action has happened yet."""
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_id = _create_published_event(client, admin_token)

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.post(
        f"/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={},
        headers=_auth(alumni_token),
    )
    assert response.status_code == 201
    assert response.json()["status"] == "requested"
    assert response.json()["message"] is None


def test_submitted_request_is_tied_to_the_authenticated_alumnus(client, admin_user, alumni_user, alumni_record, other_organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_id = _create_published_event(client, admin_token)

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.post(
        f"/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={},
        headers=_auth(alumni_token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["alumni_id"] == alumni_record.id


def test_alumni_id_cannot_be_spoofed_via_request_body(
    client, db_session, admin_user, alumni_user, alumni_record, other_organization
):
    """Even if a client sends an `alumni_id` field, the request must
    always be created for the CALLER's own linked alumni record - never
    the id supplied in the payload."""
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_id = _create_published_event(client, admin_token)

    # A second, unrelated alumni record the caller does NOT own.
    other_token, other_alumni_id = _confirmed_alumni_id(
        client, db_session, other_organization, admin_token, "spoofedtarget",
    )
    assert other_alumni_id != alumni_record.id

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.post(
        f"/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={"alumni_id": other_alumni_id, "message": "Trying to spoof"},
        headers=_auth(alumni_token),
    )
    assert response.status_code == 201, response.text
    # Tied to the authenticated caller's own alumni record, not the
    # spoofed id from the payload.
    assert response.json()["alumni_id"] == alumni_record.id
    assert response.json()["alumni_id"] != other_alumni_id


def test_admin_cannot_submit_a_speaker_request(client, admin_user, other_organization):
    """An admin account must never be able to submit a speaker request
    merely because it's authenticated - even one endpoint over from the
    admin-only select/unselect actions it IS allowed to use."""
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_id = _create_published_event(client, admin_token)

    response = client.post(
        f"/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={},
        headers=_auth(admin_token),
    )
    assert response.status_code == 403


def test_unlinked_alumni_account_is_rejected_safely(client, db_session, admin_user, organization, other_organization):
    """An `alumni`-role account with no linked Alumni record (the same
    account shape used throughout tests/test_profile_linking.py before a
    match is confirmed) gets the SAME 404 pattern already used by every
    other self-service endpoint for this case - no new error shape."""
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_id = _create_published_event(client, admin_token)

    unlinked_token = _register_alumni_user(client, db_session, organization, username="unlinkedalum")
    response = client.post(
        f"/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={},
        headers=_auth(unlinked_token),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "No alumni profile is linked to this account"


def test_database_enforces_unique_event_alumni_constraint_independent_of_app_logic(
    client, db_session, admin_user, alumni_user, alumni_record, other_organization
):
    """Proves the uniqueness guarantee is a REAL database constraint
    (uq_speaker_request_event_alumni), not merely the application-level
    "return existing row" check in the router - i.e. it would still hold
    even under a race or a direct/bulk insert that bypassed the API."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_id = _create_published_event(client, admin_token)

    db_session.add(EventSpeakerRequest(
        organization_id=other_organization.id, event_id=event_id, alumni_id=alumni_record.id,
    ))
    db_session.commit()

    db_session.add(EventSpeakerRequest(
        organization_id=other_organization.id, event_id=event_id, alumni_id=alumni_record.id,
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_alumni_cannot_submit_for_event_in_a_different_organization(
    client, admin_user, alumni_user, organization, other_organization
):
    """Event/organization scoping for submission: the event only exists
    under fsu-cci, so asking about it under stars-national (a valid,
    non-hidden organization the alumnus CAN otherwise access) must never
    resolve to it - a plain 404, never a cross-organization match."""
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_id = _create_published_event(client, admin_token, organization_slug="fsu-cci")

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.post(
        f"/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={},
        headers=_auth(alumni_token),
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------
# Admin listing
# --------------------------------------------------------------------------


def test_admin_can_list_speaker_requests_for_an_event(client, admin_user, alumni_user, other_organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_id = _create_published_event(client, admin_token)

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    client.post(
        f"/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={"message": "Pick me!"},
        headers=_auth(alumni_token),
    )

    response = client.get(
        f"/admin/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        headers=_auth(admin_token),
    )
    assert response.status_code == 200, response.text
    requests = response.json()
    assert len(requests) == 1
    assert requests[0]["alumni_full_name"] == "Jordan Doe"
    assert requests[0]["status"] == "requested"


def test_alumni_role_cannot_list_speaker_requests(client, admin_user, alumni_user, other_organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_id = _create_published_event(client, admin_token)

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get(
        f"/admin/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        headers=_auth(alumni_token),
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------
# Admin profile access (public fields only)
# --------------------------------------------------------------------------


def test_admin_can_view_only_public_fields_of_requester_profile(
    client, db_session, organization, admin_user, other_organization
):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    # A confirmed, privacy-gated UserProfile - reusing the exact helper
    # from tests/test_profile_linking.py so this exercises the SAME
    # confirmed-link machinery as the existing public-profile tests.
    _, alumni_id = _confirmed_alumni_id(
        client, db_session, organization, admin_token, "speakerowner",
        headline="Product leader", bio="Hello world",
    )
    owner_token = login(client, "speakerowner", "NewAlumPass123!")
    # Explicitly opt in to sharing email; birthday/phone remain private.
    client.put("/profile/me/privacy", json={"show_email": True}, headers=_auth(owner_token))
    client.put("/profile/me", json={"birthday": "1990-01-01"}, headers=_auth(owner_token))

    # Event lives in a DIFFERENT organization (stars-national) than the
    # requester's alumni org link (fsu-cci, via `organization` fixture) -
    # the request itself is created directly to isolate this test from
    # the submission endpoint's own org-membership behavior.
    event = Event(
        organization_id=other_organization.id, title="Panel", is_published=True,
        start_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    db_session.add(event)
    db_session.flush()
    speaker_request = EventSpeakerRequest(
        organization_id=other_organization.id, event_id=event.id, alumni_id=alumni_id,
    )
    db_session.add(speaker_request)
    db_session.commit()

    response = client.get(
        f"/admin/events/{event.id}/speaker-requests/{speaker_request.id}/alumni-profile",
        params={"organization": "stars-national"},
        headers=_auth(admin_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Public / opted-in fields ARE present.
    assert body["headline"] == "Product leader"
    assert body["bio"] == "Hello world"
    assert body["email"] is not None

    # Private, non-opted-in fields are NEVER present.
    assert body["birthday"] is None
    assert body["phone_number"] is None

    # No auth/account metadata is ever included - PublicProfileOut has no
    # such fields at all (username, password_hash, role, etc.).
    assert "password_hash" not in body
    assert "username" not in body
    assert "role" not in body


def test_admin_profile_view_matches_existing_public_profile_endpoint(
    client, db_session, organization, admin_user, other_organization
):
    """Regression: the admin-facing profile view must return byte-identical
    output to the existing self-service public-profile endpoint for the
    same alumnus - proving no separate/parallel privacy model exists."""
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _, alumni_id = _confirmed_alumni_id(
        client, db_session, organization, admin_token, "twinowner", headline="Twin headline",
    )

    event = Event(
        organization_id=other_organization.id, title="Panel", is_published=True,
        start_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    db_session.add(event)
    db_session.flush()
    speaker_request = EventSpeakerRequest(organization_id=other_organization.id, event_id=event.id, alumni_id=alumni_id)
    db_session.add(speaker_request)
    db_session.commit()

    direct = client.get(f"/public-profiles/{alumni_id}", headers=_auth(admin_token))
    via_admin = client.get(
        f"/admin/events/{event.id}/speaker-requests/{speaker_request.id}/alumni-profile",
        params={"organization": "stars-national"},
        headers=_auth(admin_token),
    )
    assert direct.status_code == 200
    assert via_admin.status_code == 200
    assert direct.json() == via_admin.json()


# --------------------------------------------------------------------------
# Selection / undo
# --------------------------------------------------------------------------


def test_admin_can_select_and_unselect_a_requester(client, admin_user, alumni_user, other_organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_id = _create_published_event(client, admin_token)

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    create_response = client.post(
        f"/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={},
        headers=_auth(alumni_token),
    )
    request_id = create_response.json()["id"]

    select_response = client.post(
        f"/admin/events/{event_id}/speaker-requests/{request_id}/select",
        params={"organization": "stars-national"},
        headers=_auth(admin_token),
    )
    assert select_response.status_code == 200, select_response.text
    assert select_response.json()["status"] == "selected"
    assert select_response.json()["selected_by_user_id"] == admin_user.id
    assert select_response.json()["selected_at"] is not None

    unselect_response = client.post(
        f"/admin/events/{event_id}/speaker-requests/{request_id}/unselect",
        params={"organization": "stars-national"},
        headers=_auth(admin_token),
    )
    assert unselect_response.status_code == 200, unselect_response.text
    assert unselect_response.json()["status"] == "requested"
    assert unselect_response.json()["selected_by_user_id"] is None
    assert unselect_response.json()["selected_at"] is None


def test_alumni_role_cannot_select_or_unselect(client, admin_user, alumni_user, other_organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_id = _create_published_event(client, admin_token)

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    create_response = client.post(
        f"/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={},
        headers=_auth(alumni_token),
    )
    request_id = create_response.json()["id"]

    select_response = client.post(
        f"/admin/events/{event_id}/speaker-requests/{request_id}/select",
        params={"organization": "stars-national"},
        headers=_auth(alumni_token),
    )
    assert select_response.status_code == 403

    unselect_response = client.post(
        f"/admin/events/{event_id}/speaker-requests/{request_id}/unselect",
        params={"organization": "stars-national"},
        headers=_auth(alumni_token),
    )
    assert unselect_response.status_code == 403


# --------------------------------------------------------------------------
# Organization / event scoping
# --------------------------------------------------------------------------


def test_wrong_organization_admin_cannot_view_or_select_request(
    client, db_session, admin_user, alumni_user, organization, other_organization
):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    # Event + request live in stars-national.
    event_id = _create_published_event(client, admin_token, organization_slug="stars-national")
    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    create_response = client.post(
        f"/events/{event_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={},
        headers=_auth(alumni_token),
    )
    request_id = create_response.json()["id"]

    # Same (global) admin, but now asking about a DIFFERENT organization
    # (fsu-cci) for this stars-national request/event - must be denied,
    # never confirming the request/event exists there.
    list_response = client.get(
        f"/admin/events/{event_id}/speaker-requests",
        params={"organization": "fsu-cci"},
        headers=_auth(admin_token),
    )
    assert list_response.status_code == 404

    profile_response = client.get(
        f"/admin/events/{event_id}/speaker-requests/{request_id}/alumni-profile",
        params={"organization": "fsu-cci"},
        headers=_auth(admin_token),
    )
    assert profile_response.status_code == 404

    select_response = client.post(
        f"/admin/events/{event_id}/speaker-requests/{request_id}/select",
        params={"organization": "fsu-cci"},
        headers=_auth(admin_token),
    )
    assert select_response.status_code == 404


def test_mismatched_event_and_request_ids_are_rejected(client, admin_user, alumni_user, other_organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    event_one_id = _create_published_event(client, admin_token, title="Event One")
    event_two_id = _create_published_event(client, admin_token, title="Event Two")

    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    create_response = client.post(
        f"/events/{event_one_id}/speaker-requests",
        params={"organization": "stars-national"},
        json={},
        headers=_auth(alumni_token),
    )
    request_id = create_response.json()["id"]

    # request_id belongs to event_one, not event_two.
    response = client.post(
        f"/admin/events/{event_two_id}/speaker-requests/{request_id}/select",
        params={"organization": "stars-national"},
        headers=_auth(admin_token),
    )
    assert response.status_code == 404


def test_unknown_event_id_is_rejected(client, admin_user, other_organization):
    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.get(
        "/admin/events/00000000-0000-0000-0000-000000000000/speaker-requests",
        params={"organization": "stars-national"},
        headers=_auth(admin_token),
    )
    assert response.status_code == 404
