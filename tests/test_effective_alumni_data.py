"""
Tests for:
  - unlinked users always getting an editable profile (GET/PUT /profile/me)
  - the expanded "full name + 2 signals" match policy
  - the "effective alumni data" layer (profile overrides flowing into
    the directory, analytics, public profile, and export - without ever
    modifying the imported Alumni row)
  - privacy enforcement and CSV-reimport safety for that layer

Additive-only: nothing here depends on changing authentication, the CSV
import pipeline, or pre-existing analytics behavior for unlinked data.
"""
from app.models.alumni import Alumni
from tests.test_import import _login, _upload
from tests.test_profile_linking import (
    _auth,
    _csv_row,
    _get_my_profile,
    _put_my_profile,
    _register_alumni_user,
    _upload_csv,
)


def _summary(client, token):
    response = client.get("/analytics/summary", params={"organization": "fsu-cci"}, headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def _names(named_counts):
    return {row["name"] for row in named_counts}


def _confirm(client, token):
    result = client.post("/profile/me/find-match", headers=_auth(token)).json()
    assert result["candidates"], result
    alumni_id = result["candidates"][0]["alumni_id"]
    response = client.post(f"/profile/me/confirm-match/{alumni_id}", headers=_auth(token))
    assert response.status_code == 200, response.text
    return alumni_id


# --------------------------------------------------------------------------
# 1. Unlinked users always get an editable profile
# --------------------------------------------------------------------------


def test_unlinked_user_can_retrieve_an_empty_editable_profile(client, organization, admin_user, db_session):
    token = _register_alumni_user(client, db_session, organization, username="brandnew")
    response = client.get("/profile/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_linked"] is False
    assert body["profile"]["alumni_id"] is None
    assert body["profile"]["link_status"] == "unmatched"
    assert body["profile"]["first_name"] is None


def test_unlinked_user_can_save_a_profile(client, organization, admin_user, db_session):
    token = _register_alumni_user(client, db_session, organization, username="savesolo")
    profile = _put_my_profile(
        client, token, first_name="Solo", last_name="Saver", current_employer="Independent Co",
    )
    assert profile["first_name"] == "Solo"
    assert profile["current_employer"] == "Independent Co"
    assert profile["alumni_id"] is None
    assert profile["link_status"] == "unmatched"


def test_profile_save_does_not_require_alumni_id(client, organization, admin_user, db_session):
    token = _register_alumni_user(client, db_session, organization, username="noalumniid")
    # No `alumni_id` field is ever sent - it is not part of
    # UserProfileUpdateRequest at all, and the save still succeeds.
    response = client.put("/profile/me", json={"headline": "Just here"}, headers=_auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profile"]["headline"] == "Just here"
    assert body["profile"]["alumni_id"] is None


# --------------------------------------------------------------------------
# 2. Expanded match policy: full name + 2 of {university, title, employer,
#    location, email, linkedin, graduation year}
# --------------------------------------------------------------------------


def test_name_plus_school_plus_employer_creates_a_candidate(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(first="Pat", last="Rivers", email="pat.rivers@example.com")])

    token = _register_alumni_user(client, db_session, organization, username="patrivers")
    _put_my_profile(
        client, token,
        first_name="Pat", last_name="Rivers",
        current_university="Florida State University", current_employer="Acme Inc",
    )
    result = client.post("/profile/me/find-match", headers=_auth(token)).json()
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["match_type"] == "standard"
    assert "full_name" in candidate["matched_fields"]
    assert "university" in candidate["matched_fields"]
    assert "current_employer" in candidate["matched_fields"]


def test_name_plus_title_plus_company_creates_a_candidate(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(first="Robin", last="Shaw", email="robin.shaw@example.com")])

    token = _register_alumni_user(client, db_session, organization, username="robinshaw")
    _put_my_profile(
        client, token,
        first_name="Robin", last_name="Shaw",
        current_job_title="Software Engineer", current_employer="Acme Inc",
    )
    result = client.post("/profile/me/find-match", headers=_auth(token)).json()
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["match_type"] == "standard"
    assert "job_title" in candidate["matched_fields"]
    assert "current_employer" in candidate["matched_fields"]


def test_name_alone_is_not_enough_to_qualify(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(first="Nolan", last="Ortiz", email="nolan.ortiz@example.com")])

    token = _register_alumni_user(client, db_session, organization, username="nolanortiz")
    _put_my_profile(client, token, first_name="Nolan", last_name="Ortiz")
    result = client.post("/profile/me/find-match", headers=_auth(token)).json()
    assert result["candidates"] == []


# --------------------------------------------------------------------------
# 3. Candidates never applied before confirmation
# --------------------------------------------------------------------------


def test_candidate_is_not_applied_before_confirmation(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(
        client, admin_token,
        [_csv_row(first="Casey", last="Bloom", email="casey.bloom@example.com", company="Acme Inc")],
    )

    token = _register_alumni_user(client, db_session, organization, username="caseybloom")
    _put_my_profile(
        client, token,
        first_name="Casey", last_name="Bloom",
        current_university="Florida State University", current_employer="Totally Different Employer",
    )
    client.post("/profile/me/find-match", headers=_auth(token))

    summary = _summary(client, admin_token)
    # The unconfirmed candidate's employer override must NOT appear in
    # top companies - only the imported "Acme Inc" does.
    assert "Totally Different Employer" not in _names(summary["top_companies"])
    assert "Acme Inc" in _names(summary["top_companies"])


def test_unconfirmed_profile_changes_do_not_affect_analytics(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(first="Drew", last="Pace", email="drew.pace@example.com")])

    token = _register_alumni_user(client, db_session, organization, username="drewpace")
    _put_my_profile(client, token, current_city="Nowhereville", current_state="ZZ")

    summary = _summary(client, admin_token)
    cities = {row["city"] for row in summary["cities"]}
    assert "Nowhereville" not in cities


# --------------------------------------------------------------------------
# 4. Confirmation creates a link
# --------------------------------------------------------------------------


def test_confirmation_creates_a_link(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(first="Ellis", last="Vega", email="ellis.vega@example.com")])

    token = _register_alumni_user(client, db_session, organization, username="ellisvega")
    _put_my_profile(client, token, primary_email="ellis.vega@example.com")
    _confirm(client, token)

    profile = _get_my_profile(client, token)
    assert profile["link_status"] == "user_confirmed"
    assert profile["alumni_id"] is not None


# --------------------------------------------------------------------------
# 5/6. Effective alumni data flows into analytics/directory once confirmed
# --------------------------------------------------------------------------


def test_linked_company_update_changes_top_company_analytics(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(
        client, admin_token,
        [_csv_row(first="Skye", last="Nolan", email="skye.nolan@example.com", company="Old Employer LLC")],
    )

    token = _register_alumni_user(client, db_session, organization, username="skyenolan")
    _put_my_profile(client, token, primary_email="skye.nolan@example.com")
    _confirm(client, token)

    _put_my_profile(client, token, current_employer="New Employer Inc")

    summary = _summary(client, admin_token)
    companies = _names(summary["top_companies"])
    assert "New Employer Inc" in companies
    assert "Old Employer LLC" not in companies


def test_linked_title_update_changes_seniority_analytics(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(
        client, admin_token,
        [_csv_row(first="Toni", last="Reyes", email="toni.reyes@example.com", title="Software Engineer")],
    )

    token = _register_alumni_user(client, db_session, organization, username="tonireyes")
    _put_my_profile(client, token, primary_email="toni.reyes@example.com")
    _confirm(client, token)

    before = _summary(client, admin_token)
    # "Software Engineer" matches no seniority keyword rule, so it starts
    # unclassified.
    assert "Vice President" not in _names(before["seniority"])

    _put_my_profile(client, token, current_job_title="Vice President of Engineering")

    after = _summary(client, admin_token)
    assert "Vice President" in _names(after["seniority"])


def test_linked_location_update_changes_map_analytics(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(
        client, admin_token,
        [_csv_row(first="Marlo", last="Finch", email="marlo.finch@example.com", city="Tallahassee", state="FL")],
    )

    token = _register_alumni_user(client, db_session, organization, username="marlofinch")
    _put_my_profile(client, token, primary_email="marlo.finch@example.com")
    _confirm(client, token)

    _put_my_profile(client, token, current_city="Miami", current_state="FL")

    summary = _summary(client, admin_token)
    cities = {row["city"] for row in summary["cities"]}
    assert "Miami" in cities
    assert "Tallahassee" not in cities


def test_linked_university_update_changes_university_analytics(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(first="Quinn", last="Ashby", email="quinn.ashby@example.com")])

    token = _register_alumni_user(client, db_session, organization, username="quinnashby")
    _put_my_profile(client, token, primary_email="quinn.ashby@example.com")
    _confirm(client, token)

    _put_my_profile(client, token, current_university="University of Miami")

    summary = _summary(client, admin_token)
    universities = _names(summary["universities"])
    assert "University of Miami" in universities
    assert "Florida State University" not in universities


def test_linked_directory_reflects_effective_values(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(first="Ira", last="Okafor", email="ira.okafor@example.com")])

    token = _register_alumni_user(client, db_session, organization, username="iraokafor")
    _put_my_profile(client, token, primary_email="ira.okafor@example.com")
    alumni_id = _confirm(client, token)
    _put_my_profile(client, token, current_employer="Directory Effective Co")

    response = client.get("/alumni-data", params={"organization": "fsu-cci"}, headers=_auth(admin_token))
    rows = {row["id"]: row for row in response.json()["data"]}
    assert rows[alumni_id]["company"] == "Directory Effective Co"

    # The imported value is untouched underneath.
    alumni = db_session.get(Alumni, alumni_id)
    db_session.refresh(alumni)
    assert alumni.company == "Acme Inc"


# --------------------------------------------------------------------------
# 7. Privacy is respected server-side for effective values
# --------------------------------------------------------------------------


def test_private_fields_never_appear_publicly(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(first="Zora", last="Kim", email="zora.kim@example.com")])

    token = _register_alumni_user(client, db_session, organization, username="zorakim")
    _put_my_profile(client, token, primary_email="zora.kim@example.com")
    alumni_id = _confirm(client, token)

    _put_my_profile(client, token, current_employer="Private Employer Co", phone_number="555-0100")
    client.put("/profile/me/privacy", json={"show_current_employer": False}, headers=_auth(token))

    response = client.get(f"/public-profiles/{alumni_id}", headers=_auth(admin_token))
    body = response.json()
    assert body["company"] != "Private Employer Co"
    assert body["phone_number"] is None

    # Analytics must not surface the private override either.
    summary = _summary(client, admin_token)
    assert "Private Employer Co" not in _names(summary["top_companies"])


# --------------------------------------------------------------------------
# 8. CSV reimport safety
# --------------------------------------------------------------------------


def test_csv_reimport_does_not_delete_profile_data(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, admin_token, [_csv_row(first="Wes", last="Hollis", email="wes.hollis@example.com")])

    token = _register_alumni_user(client, db_session, organization, username="weshollis")
    _put_my_profile(client, token, primary_email="wes.hollis@example.com", headline="Persisted headline")
    _confirm(client, token)

    _upload_csv(client, admin_token, [_csv_row(first="Wes", last="Hollis", email="wes.hollis@example.com")])

    profile = _get_my_profile(client, token)
    assert profile["headline"] == "Persisted headline"
    assert profile["link_status"] == "user_confirmed"


def test_inactive_linked_alumni_stops_affecting_active_analytics(client, organization, admin_user, db_session):
    admin_token = _login(client, "admin", "AdminPass123!")
    _upload_csv(
        client, admin_token,
        [_csv_row(first="Nia", last="Brandt", email="nia.brandt@example.com", linkedin="https://linkedin.com/in/nia-brandt")],
    )

    token = _register_alumni_user(client, db_session, organization, username="niabrandt")
    _put_my_profile(client, token, primary_email="nia.brandt@example.com", current_employer="Nia Effective Co")
    _confirm(client, token)

    before = _summary(client, admin_token)
    assert "Nia Effective Co" in _names(before["top_companies"])

    # Replace-mode import that drops this person entirely - their Alumni
    # row is deactivated (never deleted, never reassigned).
    _upload_csv(
        client, admin_token,
        [_csv_row(
            first="Someone", last="Else", email="someoneelse2@example.com",
            linkedin="https://linkedin.com/in/someone-else-2",
        )],
    )

    after = _summary(client, admin_token)
    assert "Nia Effective Co" not in _names(after["top_companies"])

    profile = _get_my_profile(client, token)
    assert profile["needs_review"] is True
