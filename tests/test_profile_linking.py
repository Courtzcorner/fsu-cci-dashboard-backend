"""
Tests for the alumni account profile + directory linking system
(app.models.user_profile, app.services.identity_matching_service,
app.services.profile_link_service, app.routers.user_profile_routes,
app.routers.public_profile_routes, and the new admin profile-link
endpoints).

This feature is additive-only: none of these tests touch or depend on
changing the authentication flow, CSV import pipeline, or existing
analytics - they only prove the new profile/matching system works
correctly on top of them.
"""
from app.models.alumni import Alumni
from app.models.user_profile import UserProfile
from tests.test_import import _login, _upload

CANONICAL_HEADER = (
    "Last Name,First Name,Email,Linkedin URL,Company Name,Job Title,City,State,"
    "Notes,Verification Status,Verification Date,Education"
)


def _csv_row(
    last="Lee", first="Jordan", email="jordan.lee@example.com",
    linkedin="https://linkedin.com/in/jordan-lee", company="Acme Inc", title="Software Engineer",
    city="Tallahassee", state="FL", university="Florida State University",
):
    return f"{last},{first},{email},{linkedin},{company},{title},{city},{state},,Verified,2026-01-01,{university}"


def _upload_csv(client, token, rows):
    csv_text = "\n".join([CANONICAL_HEADER, *rows]) + "\n"
    response = _upload(client, token, csv_text)
    assert response.status_code == 200, response.text
    return response.json()


def _register_alumni_user(client, db_session, organization, username="newalum", password="NewAlumPass123!"):
    """A brand-new login account with NO pre-existing users.alumni_id
    link - this is exactly the account type the new self-service
    matching system is for."""
    from app.security import hash_password
    from app.models.user import User

    user = User(username=username, password_hash=hash_password(password), role="alumni")
    db_session.add(user)
    db_session.commit()
    return _login(client, username, password)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _put_my_profile(client, token, **fields):
    response = client.put("/profile/me", json=fields, headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# Strong-signal matching (1-2)
# --------------------------------------------------------------------------


def test_exact_email_produces_one_candidate(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(email="unique.email@example.com")])

    user_token = _register_alumni_user(client, db_session, organization)
    _put_my_profile(client, user_token, primary_email="unique.email@example.com")

    response = client.post("/profile/me/find-match", headers=_auth(user_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["match_type"] == "strong"
    assert "email_exact" in body["candidates"][0]["matched_signals"]


def test_exact_linkedin_produces_one_candidate(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(linkedin="https://www.linkedin.com/in/jordan-lee/?trk=abc")])

    user_token = _register_alumni_user(client, db_session, organization)
    _put_my_profile(client, user_token, linkedin_url="linkedin.com/in/jordan-lee")

    response = client.post("/profile/me/find-match", headers=_auth(user_token))
    body = response.json()
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["match_type"] == "strong"
    assert "linkedin_exact" in body["candidates"][0]["matched_signals"]


# --------------------------------------------------------------------------
# Standard-signal matching: name + >=2 of (university, employer, title, city/state) (3-5)
# --------------------------------------------------------------------------


def test_name_plus_university_plus_employer_produces_candidate(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(
        client, admin_token,
        [_csv_row(first="Taylor", last="Reed", email="tr@example.com", linkedin="https://linkedin.com/in/tr",
                   company="Blue Cross", title="Consultant", university="Florida State University")],
    )

    user_token = _register_alumni_user(client, db_session, organization)
    _put_my_profile(
        client, user_token, first_name="Taylor", last_name="Reed",
        current_university="Florida State University", current_employer="Blue Cross",
    )

    response = client.post("/profile/me/find-match", headers=_auth(user_token))
    body = response.json()
    assert len(body["candidates"]) == 1
    candidate = body["candidates"][0]
    assert candidate["match_type"] == "standard"
    assert "full_name_exact" in candidate["matched_signals"]
    assert "university_exact" in candidate["matched_signals"]
    assert "employer_exact" in candidate["matched_signals"]


def test_name_plus_university_plus_job_title_produces_candidate(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(
        client, admin_token,
        [_csv_row(first="Morgan", last="Blake", email="mb@example.com", linkedin="https://linkedin.com/in/mb",
                   title="Strategy Consultant II", university="Florida State University", company="Unrelated Co")],
    )

    user_token = _register_alumni_user(client, db_session, organization)
    _put_my_profile(
        client, user_token, first_name="Morgan", last_name="Blake",
        current_university="Florida State University", current_job_title="Strategy Consultant II",
    )

    response = client.post("/profile/me/find-match", headers=_auth(user_token))
    body = response.json()
    assert len(body["candidates"]) == 1
    candidate = body["candidates"][0]
    assert candidate["match_type"] == "standard"
    assert "job_title_exact" in candidate["matched_signals"]
    assert "university_exact" in candidate["matched_signals"]


def test_employer_title_university_without_matching_name_does_not_qualify(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(
        client, admin_token,
        [_csv_row(first="Casey", last="Nguyen", email="cn@example.com", linkedin="https://linkedin.com/in/cn",
                   company="Acme Inc", title="Software Engineer", university="Florida State University")],
    )

    user_token = _register_alumni_user(client, db_session, organization)
    # Same employer, title, and university - but a completely different name.
    _put_my_profile(
        client, user_token, first_name="Completely", last_name="Different",
        current_university="Florida State University", current_employer="Acme Inc",
        current_job_title="Software Engineer",
    )

    response = client.post("/profile/me/find-match", headers=_auth(user_token))
    body = response.json()
    assert body["candidates"] == []
    assert body["link_status"] == "unmatched"


# --------------------------------------------------------------------------
# Multiple candidates / confirmation / conflict (6-8)
# --------------------------------------------------------------------------


def test_two_qualifying_alumni_returns_both_candidates_for_selection(client, organization, admin_user, db_session):
    """Two DISTINCT alumni (different names/employers) each independently
    qualify against the same profile - one via an exact email match, the
    other via an exact LinkedIn match. Both must be surfaced for the
    user to choose, never auto-linked to either."""
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(
        client, admin_token,
        [
            _csv_row(first="Pat", last="Nguyen", email="shared.email@example.com",
                      linkedin="https://linkedin.com/in/pat-nguyen", company="Globex"),
            _csv_row(first="Robin", last="Osei", email="robin.osei@example.com",
                      linkedin="https://linkedin.com/in/shared-linkedin", company="Initech"),
        ],
    )

    user_token = _register_alumni_user(client, db_session, organization)
    _put_my_profile(
        client, user_token, primary_email="shared.email@example.com",
        linkedin_url="https://linkedin.com/in/shared-linkedin",
    )

    response = client.post("/profile/me/find-match", headers=_auth(user_token))
    body = response.json()
    assert len(body["candidates"]) == 2
    assert body["link_status"] == "candidate"


def test_user_confirmation_creates_the_link(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(email="confirm.me@example.com")])

    user_token = _register_alumni_user(client, db_session, organization)
    _put_my_profile(client, user_token, primary_email="confirm.me@example.com")
    candidates = client.post("/profile/me/find-match", headers=_auth(user_token)).json()["candidates"]
    alumni_id = candidates[0]["alumni_id"]

    response = client.post(f"/profile/me/confirm-match/{alumni_id}", headers=_auth(user_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["link_status"] == "user_confirmed"
    assert body["alumni_id"] == alumni_id

    profile_response = client.get("/profile/me", headers=_auth(user_token))
    assert profile_response.json()["alumni_id"] == alumni_id
    assert profile_response.json()["link_status"] == "user_confirmed"


def test_second_user_cannot_claim_already_confirmed_alumni(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(email="claimed@example.com")])

    first_token = _register_alumni_user(client, db_session, organization, username="firstclaim")
    _put_my_profile(client, first_token, primary_email="claimed@example.com")
    alumni_id = client.post("/profile/me/find-match", headers=_auth(first_token)).json()["candidates"][0]["alumni_id"]
    confirm_response = client.post(f"/profile/me/confirm-match/{alumni_id}", headers=_auth(first_token))
    assert confirm_response.status_code == 200

    second_token = _register_alumni_user(client, db_session, organization, username="secondclaim")
    _put_my_profile(client, second_token, primary_email="claimed@example.com")
    client.post("/profile/me/find-match", headers=_auth(second_token))

    second_confirm = client.post(f"/profile/me/confirm-match/{alumni_id}", headers=_auth(second_token))
    assert second_confirm.status_code == 409

    second_profile = client.get("/profile/me", headers=_auth(second_token)).json()
    assert second_profile["link_status"] == "conflict"
    assert second_profile["alumni_id"] is None

    # The first (legitimate) confirmation is untouched.
    first_profile = client.get("/profile/me", headers=_auth(first_token)).json()
    assert first_profile["link_status"] == "user_confirmed"
    assert first_profile["alumni_id"] == alumni_id


# --------------------------------------------------------------------------
# Privacy enforcement (9-11)
# --------------------------------------------------------------------------


def _confirmed_alumni_id(client, db_session, organization, admin_token, username, **profile_fields):
    email = profile_fields.pop("_email", f"{username}@example.com")
    _upload_csv(client, admin_token, [_csv_row(email=email, first=username, last="Test")])
    token = _register_alumni_user(client, db_session, organization, username=username)
    _put_my_profile(client, token, first_name=username, last_name="Test", primary_email=email, **profile_fields)
    alumni_id = client.post("/profile/me/find-match", headers=_auth(token)).json()["candidates"][0]["alumni_id"]
    client.post(f"/profile/me/confirm-match/{alumni_id}", headers=_auth(token))
    return token, alumni_id


def test_private_email_excluded_from_public_profile(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    owner_token, alumni_id = _confirmed_alumni_id(client, db_session, organization, admin_token, "privacyowner")
    # show_email defaults to False - never opted in.

    viewer_token = _login(client, "admin", "AdminPass123!")
    response = client.get(f"/public-profiles/{alumni_id}", headers=_auth(viewer_token))
    assert response.status_code == 200
    assert response.json()["email"] is None


def test_private_birthday_excluded_from_public_profile(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    owner_token, alumni_id = _confirmed_alumni_id(client, db_session, organization, admin_token, "birthdayowner")
    client.put("/profile/me", json={"birthday": "1990-01-01"}, headers=_auth(owner_token))

    response = client.get(f"/public-profiles/{alumni_id}", headers=_auth(admin_token))
    assert response.json()["birthday"] is None


def test_opted_in_fields_are_returned_publicly(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    owner_token, alumni_id = _confirmed_alumni_id(
        client, db_session, organization, admin_token, "optedin", headline="Product leader", bio="Hello world",
    )
    # LinkedIn is public by default.
    client.put("/profile/me", json={"linkedin_url": "https://linkedin.com/in/optedin"}, headers=_auth(owner_token))
    # Explicitly opt in to sharing email.
    client.put("/profile/me/privacy", json={"show_email": True}, headers=_auth(owner_token))

    response = client.get(f"/public-profiles/{alumni_id}", headers=_auth(admin_token))
    body = response.json()
    assert body["headline"] == "Product leader"
    assert body["bio"] == "Hello world"
    assert body["linkedin_url"] == "https://linkedin.com/in/optedin"
    assert body["email"] == "optedin@example.com"
    assert body["has_user_profile"] is True


# --------------------------------------------------------------------------
# Unlinking / CSV reimport safety (12-14)
# --------------------------------------------------------------------------


def test_unlinking_does_not_delete_either_record(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    owner_token, alumni_id = _confirmed_alumni_id(client, db_session, organization, admin_token, "unlinkme")

    response = client.delete("/profile/me/link", headers=_auth(owner_token))
    assert response.status_code == 200
    assert response.json()["link_status"] == "unmatched"

    profile = client.get("/profile/me", headers=_auth(owner_token)).json()
    assert profile["alumni_id"] is None

    assert db_session.get(Alumni, alumni_id) is not None
    user_profile_row = db_session.query(UserProfile).filter(UserProfile.id == profile["id"]).first()
    assert user_profile_row is not None


def test_csv_reimport_preserves_a_valid_link(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    owner_token, alumni_id = _confirmed_alumni_id(client, db_session, organization, admin_token, "stableid")

    # Reimport a CSV that still contains this same person (same LinkedIn/
    # email identity, so the importer updates the existing Alumni row
    # in place rather than creating a new one - the primary key survives).
    _upload_csv(client, admin_token, [_csv_row(email="stableid@example.com", first="stableid", last="Test")])

    profile = client.get("/profile/me", headers=_auth(owner_token)).json()
    assert profile["alumni_id"] == alumni_id
    assert profile["link_status"] == "user_confirmed"
    assert profile["needs_review"] is False


def test_deactivated_linked_alumni_is_marked_for_review(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    owner_token, alumni_id = _confirmed_alumni_id(client, db_session, organization, admin_token, "willbedropped")

    # Replace-mode import that no longer includes this person at all -
    # their Alumni row becomes is_active=False, but is never deleted and
    # never reassigned to a different confirmed record.
    _upload_csv(
        client, admin_token,
        [_csv_row(
            email="someoneelse@example.com", first="Someone", last="Else",
            linkedin="https://linkedin.com/in/someone-else",
        )],
    )

    alumni = db_session.get(Alumni, alumni_id)
    assert alumni is not None
    assert alumni.is_active is False

    profile = client.get("/profile/me", headers=_auth(owner_token)).json()
    assert profile["alumni_id"] == alumni_id  # never silently reassigned
    assert profile["needs_review"] is True

    admin_queue = client.get("/admin/profile-match-candidates", headers=_auth(admin_token))
    assert admin_queue.status_code == 200
    flagged_ids = [row["user_profile_id"] for row in admin_queue.json()]
    assert profile["id"] in flagged_ids


# --------------------------------------------------------------------------
# Directory extension + admin approve/reject additive endpoints
# --------------------------------------------------------------------------


def test_directory_reports_has_public_profile_for_confirmed_links(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    owner_token, alumni_id = _confirmed_alumni_id(client, db_session, organization, admin_token, "directorylinked")

    response = client.get("/alumni-data", headers=_auth(admin_token))
    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()["data"]}
    assert rows[alumni_id]["has_public_profile"] is True
    assert rows[alumni_id]["public_profile_url"] == f"/alumni/{alumni_id}"


def test_admin_can_approve_a_candidate_link(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(email="adminapprove@example.com")])

    user_token = _register_alumni_user(client, db_session, organization, username="adminapproveuser")
    _put_my_profile(client, user_token, primary_email="adminapprove@example.com")
    result = client.post("/profile/me/find-match", headers=_auth(user_token)).json()
    alumni_id = result["candidates"][0]["alumni_id"]
    profile = client.get("/profile/me", headers=_auth(user_token)).json()

    response = client.post(
        f"/admin/profile-links/{profile['id']}/approve",
        data={"alumni_id": alumni_id},
        headers=_auth(admin_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["link_status"] == "admin_confirmed"
    assert body["alumni_id"] == alumni_id


def test_admin_can_reject_and_unlink(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    owner_token, alumni_id = _confirmed_alumni_id(client, db_session, organization, admin_token, "adminunlink")
    profile = client.get("/profile/me", headers=_auth(owner_token)).json()

    response = client.delete(f"/admin/profile-links/{profile['id']}", headers=_auth(admin_token))
    assert response.status_code == 200
    assert response.json()["link_status"] == "unmatched"

    assert db_session.get(Alumni, alumni_id) is not None


# --------------------------------------------------------------------------
# Never overwrites imported alumni data
# --------------------------------------------------------------------------


def test_profile_edits_never_write_to_alumni_table(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    owner_token, alumni_id = _confirmed_alumni_id(
        client, db_session, organization, admin_token, "noclobber", current_employer="My New Employer",
    )

    alumni = db_session.get(Alumni, alumni_id)
    db_session.refresh(alumni)
    # The imported company value is untouched by the profile's
    # current_employer field, even though they now "match".
    assert alumni.company == "Acme Inc"
