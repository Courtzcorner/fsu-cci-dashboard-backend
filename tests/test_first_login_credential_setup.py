"""
Tests for the additive first-login (temporary account) credential setup
flow: app.models.user's new columns, app.services.credential_setup_service,
app.seed.temporary_accounts, and the new
GET /auth/me / POST /auth/complete-first-login / POST /auth/logout routes.

Additive-only: none of these tests touch or depend on changing the JWT
structure beyond the new "tv" claim, password hashing, role authorization,
admin/alumni route protection, CSV import, profile linking, analytics, or
CORS - they only prove the new temporary-credential flow works correctly
on top of them.
"""
from app.models.alumni import Alumni
from app.models.user import User
from app.security import hash_password, verify_password
from app.seed.temporary_accounts import (
    BROKEN_SEED_USERNAMES,
    TEMPORARY_ACCOUNTS,
    delete_broken_seed_users,
    repair_seed_usernames,
    seed_temporary_accounts,
)
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME

TEMP_PASSWORD = "testtest"
STRONG_PASSWORD = "NewStrongPass1"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _seed(db_session, **kwargs):
    return seed_temporary_accounts(db_session, **kwargs)


def _login_temp(client, username, password=TEMP_PASSWORD):
    return client.post("/login", json={"username": username, "password": password})


# --------------------------------------------------------------------------
# 1-2. Temporary login works and flags credential setup as required
# --------------------------------------------------------------------------


def test_temporary_username_and_testtest_can_authenticate(client, db_session):
    _seed(db_session)
    response = _login_temp(client, "EllieWebb")
    assert response.status_code == 200, response.text


def test_courtneystokes_is_created_as_an_alumni_temporary_account(client, db_session):
    _seed(db_session)

    user = db_session.query(User).filter(User.username == "courtneystokes").first()
    assert user is not None
    assert user.role == "alumni"
    assert user.must_change_credentials is True
    assert user.temporary_account_created_at is not None
    # Password is hashed with the existing bcrypt utility - never stored
    # in plaintext.
    assert user.password_hash != "testtest"
    assert user.password_hash.startswith("$2b$")

    response = _login_temp(client, "courtneystokes")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["role"] == "alumni"
    assert body["user"]["must_change_credentials"] is True


def test_login_response_has_must_change_credentials_true(client, db_session):
    _seed(db_session)
    response = _login_temp(client, "EllieWebb")
    body = response.json()
    assert body["user"]["must_change_credentials"] is True
    assert body["user"]["username"] == "EllieWebb"
    assert body["user"]["role"] == "alumni"
    # The temporary password itself is never echoed back anywhere.
    assert "testtest" not in response.text


# --------------------------------------------------------------------------
# 3-4. Temporary token is server-side restricted to exactly three routes
# --------------------------------------------------------------------------


def test_temporary_token_cannot_access_dashboard_data(client, db_session, organization):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]

    response = client.get("/alumni-data", params={"organization": "fsu-cci"}, headers=_auth(token))
    assert response.status_code == 403

    response = client.get("/analytics/summary", params={"organization": "fsu-cci"}, headers=_auth(token))
    assert response.status_code == 403

    response = client.get("/profile/me", headers=_auth(token))
    assert response.status_code == 403


def test_temporary_token_cannot_access_admin_routes(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "Eberanderee").json()["access_token"]

    response = client.get("/admin/users", headers=_auth(token))
    assert response.status_code == 403


def test_temporary_token_can_call_the_three_exempted_routes(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]

    me_response = client.get("/auth/me", headers=_auth(token))
    assert me_response.status_code == 200
    assert me_response.json()["must_change_credentials"] is True

    setup_response = client.post(
        "/auth/complete-first-login",
        json={"new_username": "ellie.newname", "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD},
        headers=_auth(token),
    )
    assert setup_response.status_code == 200, setup_response.text


def test_temporary_token_can_call_logout(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "OwenV").json()["access_token"]
    response = client.post("/auth/logout", headers=_auth(token))
    assert response.status_code == 200


# --------------------------------------------------------------------------
# 5-10. Validation
# --------------------------------------------------------------------------


def test_duplicate_username_is_rejected(client, db_session, admin_user):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    response = client.post(
        "/auth/complete-first-login",
        json={"new_username": ADMIN_USERNAME, "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD},
        headers=_auth(token),
    )
    assert response.status_code == 400


def test_case_insensitive_duplicate_username_is_rejected(client, db_session, admin_user):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    response = client.post(
        "/auth/complete-first-login",
        json={
            "new_username": ADMIN_USERNAME.upper(), "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD,
        },
        headers=_auth(token),
    )
    assert response.status_code == 400


def test_weak_password_is_rejected(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    response = client.post(
        "/auth/complete-first-login",
        json={"new_username": "elliewebb2", "new_password": "weak", "confirm_password": "weak"},
        headers=_auth(token),
    )
    assert response.status_code == 400


def test_testtest_cannot_be_reused(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    response = client.post(
        "/auth/complete-first-login",
        json={"new_username": "elliewebb2", "new_password": "testtest", "confirm_password": "testtest"},
        headers=_auth(token),
    )
    assert response.status_code == 400


def test_password_confirmation_mismatch_is_rejected(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    response = client.post(
        "/auth/complete-first-login",
        json={"new_username": "elliewebb2", "new_password": STRONG_PASSWORD, "confirm_password": "SomethingElse1"},
        headers=_auth(token),
    )
    assert response.status_code == 400


def test_role_cannot_be_changed_through_the_request(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    response = client.post(
        "/auth/complete-first-login",
        json={
            "new_username": "elliewebb2", "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD,
            "role": "admin",
        },
        headers=_auth(token),
    )
    # extra="forbid" on the request schema rejects the unexpected field.
    assert response.status_code == 422


def test_blank_and_whitespace_usernames_are_rejected(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]

    response = client.post(
        "/auth/complete-first-login",
        json={"new_username": "  padded  ", "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD},
        headers=_auth(token),
    )
    assert response.status_code == 400


def test_reusing_the_temporary_username_is_rejected(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    response = client.post(
        "/auth/complete-first-login",
        json={"new_username": "EllieWebb", "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD},
        headers=_auth(token),
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# 11-14. Successful setup
# --------------------------------------------------------------------------


def test_successful_setup_updates_username_and_password(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    response = client.post(
        "/auth/complete-first-login",
        json={"new_username": "ellie.new", "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] == "sign_out_and_redirect_to_login"
    assert body["username"] == "ellie.new"
    # No new access token is ever returned - the user must sign in again.
    assert "access_token" not in body

    user = db_session.query(User).filter(User.username == "ellie.new").first()
    assert user is not None
    assert user.previous_username == "EllieWebb"
    assert user.must_change_credentials is False
    assert user.credentials_updated_at is not None
    assert user.username_changed_at is not None

    # New credentials work.
    new_login = client.post("/login", json={"username": "ellie.new", "password": STRONG_PASSWORD})
    assert new_login.status_code == 200
    assert new_login.json()["user"]["must_change_credentials"] is False


def test_successful_setup_preserves_role(client, db_session):
    _seed(db_session)
    admin_token = _login_temp(client, "Eberanderee").json()["access_token"]
    client.post(
        "/auth/complete-first-login",
        json={"new_username": "eber.new", "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD},
        headers=_auth(admin_token),
    )
    user = db_session.query(User).filter(User.username == "eber.new").first()
    assert user.role == "admin"

    new_login = client.post("/login", json={"username": "eber.new", "password": STRONG_PASSWORD})
    assert new_login.json()["user"]["role"] == "admin"


def test_setup_never_creates_a_second_account(client, db_session):
    _seed(db_session)
    original = db_session.query(User).filter(User.username == "EllieWebb").first()
    original_id = original.id

    token = _login_temp(client, "EllieWebb").json()["access_token"]
    client.post(
        "/auth/complete-first-login",
        json={"new_username": "ellie.renamed", "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD},
        headers=_auth(token),
    )

    db_session.expire_all()
    all_users = db_session.query(User).all()
    matching = [u for u in all_users if u.id == original_id]
    assert len(matching) == 1
    assert matching[0].username == "ellie.renamed"


def test_temporary_credentials_no_longer_work_afterward(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    client.post(
        "/auth/complete-first-login",
        json={"new_username": "ellie.done", "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD},
        headers=_auth(token),
    )

    # Old username/password combination is dead.
    old_login = client.post("/login", json={"username": "EllieWebb", "password": "testtest"})
    assert old_login.status_code == 401

    # The temporary token itself is revoked (token_version bumped), even
    # for the one exempted route it used to be allowed to call.
    me_response = client.get("/auth/me", headers=_auth(token))
    assert me_response.status_code == 401


def test_user_must_sign_in_again_with_new_credentials(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    client.post(
        "/auth/complete-first-login",
        json={"new_username": "ellie.again", "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD},
        headers=_auth(token),
    )
    new_login = client.post("/login", json={"username": "ellie.again", "password": STRONG_PASSWORD})
    assert new_login.status_code == 200
    new_token = new_login.json()["access_token"]
    # The freshly issued token now has full dashboard access.
    response = client.get("/profile/me", headers=_auth(new_token))
    assert response.status_code == 200


# --------------------------------------------------------------------------
# 15. Existing permanent users unaffected
# --------------------------------------------------------------------------


def test_existing_permanent_users_are_unaffected(client, admin_user, db_session):
    response = client.post("/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["must_change_credentials"] is False

    # Full dashboard/admin access still works exactly as before.
    users_response = client.get("/admin/users", headers=_auth(body["access_token"]))
    assert users_response.status_code == 200


# --------------------------------------------------------------------------
# 16-17. Seed idempotency
# --------------------------------------------------------------------------


def test_running_the_seed_twice_does_not_create_duplicates(client, db_session):
    _seed(db_session)
    _seed(db_session)
    for spec in TEMPORARY_ACCOUNTS:
        matches = db_session.query(User).filter(User.username == spec.username).all()
        assert len(matches) == 1


def test_running_the_seed_does_not_reset_completed_users(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    client.post(
        "/auth/complete-first-login",
        json={"new_username": "ellie.stable", "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD},
        headers=_auth(token),
    )

    # Re-running the seed (even with force_reset) must not touch the
    # completed account, and must not recreate "EllieWebb" as a brand-new
    # temporary account either.
    _seed(db_session, force_reset=True)

    renamed = db_session.query(User).filter(User.username == "ellie.stable").first()
    assert renamed is not None
    assert renamed.must_change_credentials is False

    reintroduced = db_session.query(User).filter(User.username == "EllieWebb").first()
    assert reintroduced is None

    # The still-pending accounts, however, ARE eligible for a force reset.
    owen = db_session.query(User).filter(User.username == "OwenV").first()
    assert owen.must_change_credentials is True


# --------------------------------------------------------------------------
# --repair-seed-usernames: fixes a pre-existing row the plain seed
# silently skipped (the reported "Invalid username or password" bug)
# --------------------------------------------------------------------------


def _make_preexisting_broken_row(db_session, username="EllieWebb", role="alumni", password="SomeUnrelatedPass1"):
    """Simulates a `User` row that existed BEFORE the temporary-account
    seed feature shipped: an ordinary account, unrelated password,
    `must_change_credentials` at its column default (False) - exactly
    the row `seed_temporary_accounts()` cannot safely distinguish from
    "already completed setup"."""
    user = User(username=username, password_hash=hash_password(password), role=role)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_preexisting_ellie_webb_account_is_repaired(client, db_session):
    broken = _make_preexisting_broken_row(db_session)

    results = repair_seed_usernames(db_session)
    outcomes = dict(results)
    assert outcomes["EllieWebb"] == "repaired"

    db_session.refresh(broken)
    assert broken.must_change_credentials is True
    assert verify_password("testtest", broken.password_hash)


def test_repair_preserves_the_same_user_id(client, db_session):
    broken = _make_preexisting_broken_row(db_session)
    original_id = broken.id

    repair_seed_usernames(db_session)

    db_session.expire_all()
    repaired = db_session.query(User).filter(User.username == "EllieWebb").first()
    assert repaired.id == original_id


def test_repair_preserves_and_validates_role(client, db_session):
    _make_preexisting_broken_row(db_session, username="EllieWebb", role="alumni")
    # A role mismatch (configured as "alumni", but this row is "admin")
    # must be flagged, never silently overwritten.
    admin_named_owen = _make_preexisting_broken_row(db_session, username="OwenV", role="admin")

    outcomes = dict(repair_seed_usernames(db_session))
    assert outcomes["EllieWebb"] == "repaired"
    assert outcomes["OwenV"] == "role_conflict"

    ellie = db_session.query(User).filter(User.username == "EllieWebb").first()
    assert ellie.role == "alumni"

    db_session.refresh(admin_named_owen)
    assert admin_named_owen.role == "admin"
    assert admin_named_owen.must_change_credentials is False


def test_testtest_works_after_repair(client, db_session):
    _make_preexisting_broken_row(db_session)
    repair_seed_usernames(db_session)

    response = client.post("/login", json={"username": "EllieWebb", "password": "testtest"})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["must_change_credentials"] is True


def test_repair_sets_must_change_credentials_true(client, db_session):
    broken = _make_preexisting_broken_row(db_session)
    assert broken.must_change_credentials is False  # sanity: starts broken

    repair_seed_usernames(db_session)
    db_session.refresh(broken)
    assert broken.must_change_credentials is True


def test_repair_does_not_touch_an_account_renamed_after_completed_setup(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    client.post(
        "/auth/complete-first-login",
        json={"new_username": "ellie.done2", "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD},
        headers=_auth(token),
    )

    renamed_before = db_session.query(User).filter(User.username == "ellie.done2").first()
    password_hash_before = renamed_before.password_hash

    outcomes = dict(repair_seed_usernames(db_session))
    assert outcomes["EllieWebb"] == "completed_and_renamed"

    db_session.refresh(renamed_before)
    assert renamed_before.username == "ellie.done2"
    assert renamed_before.password_hash == password_hash_before
    assert renamed_before.must_change_credentials is False

    # And "EllieWebb" was NOT recreated as a brand-new account.
    reintroduced = db_session.query(User).filter(User.username == "EllieWebb").first()
    assert reintroduced is None


def test_repair_does_not_touch_unrelated_users(client, admin_user, db_session):
    admin_password_hash_before = admin_user.password_hash
    admin_role_before = admin_user.role

    repair_seed_usernames(db_session)

    db_session.refresh(admin_user)
    assert admin_user.password_hash == admin_password_hash_before
    assert admin_user.role == admin_role_before
    assert admin_user.must_change_credentials is False


def test_running_the_repair_twice_creates_no_duplicates(client, db_session):
    _make_preexisting_broken_row(db_session)

    repair_seed_usernames(db_session)
    repair_seed_usernames(db_session)

    for spec in TEMPORARY_ACCOUNTS:
        matches = db_session.query(User).filter(User.username == spec.username).all()
        assert len(matches) <= 1

    ellie_matches = db_session.query(User).filter(User.username == "EllieWebb").all()
    assert len(ellie_matches) == 1


def test_repair_creates_a_wholly_missing_seed_account(client, db_session):
    outcomes = dict(repair_seed_usernames(db_session))
    assert outcomes["courtneystokes"] == "created"

    user = db_session.query(User).filter(User.username == "courtneystokes").first()
    assert user is not None
    assert user.role == "alumni"
    assert user.must_change_credentials is True


# --------------------------------------------------------------------------
# --delete-broken-seed-users: permanently deletes ONLY the five original
# broken temporary seed accounts so they can be recreated cleanly.
# --------------------------------------------------------------------------


def test_the_five_exact_usernames_are_deleted(client, db_session):
    _seed(db_session)

    outcomes = dict(delete_broken_seed_users(db_session))
    assert outcomes == {username: "deleted" for username in BROKEN_SEED_USERNAMES}

    for username in BROKEN_SEED_USERNAMES:
        assert db_session.query(User).filter(User.username == username).first() is None


def test_courtneystokes_remains_untouched_by_delete(client, db_session):
    _seed(db_session)

    delete_broken_seed_users(db_session)

    courtney = db_session.query(User).filter(User.username == "courtneystokes").first()
    assert courtney is not None
    assert courtney.must_change_credentials is True


def test_delete_does_not_touch_unrelated_users(client, admin_user, db_session):
    _seed(db_session)
    admin_password_hash_before = admin_user.password_hash
    admin_role_before = admin_user.role

    delete_broken_seed_users(db_session)

    db_session.refresh(admin_user)
    assert admin_user.password_hash == admin_password_hash_before
    assert admin_user.role == admin_role_before


def test_delete_does_not_touch_a_renamed_account(client, db_session):
    _seed(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    client.post(
        "/auth/complete-first-login",
        json={"new_username": "ellie.safe", "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD},
        headers=_auth(token),
    )

    renamed_before = db_session.query(User).filter(User.username == "ellie.safe").first()
    password_hash_before = renamed_before.password_hash

    outcomes = dict(delete_broken_seed_users(db_session))
    assert outcomes["EllieWebb"] == "skipped_renamed"

    db_session.refresh(renamed_before)
    assert renamed_before.username == "ellie.safe"
    assert renamed_before.password_hash == password_hash_before

    # The remaining four (still under their original temporary username)
    # are unaffected by "EllieWebb" being skipped.
    for username in ("Eberanderee", "Bellabozied", "OwenV", "EbeAlum"):
        assert outcomes[username] == "deleted"


def test_delete_does_not_touch_imported_alumni_records(client, db_session):
    _seed(db_session)
    alumni = Alumni(first_name="Jane", last_name="Doe", full_name="Jane Doe", is_active=True)
    db_session.add(alumni)
    db_session.commit()
    alumni_id = alumni.id

    delete_broken_seed_users(db_session)

    still_there = db_session.query(Alumni).filter(Alumni.id == alumni_id).first()
    assert still_there is not None
    assert still_there.is_active is True


def test_running_the_delete_twice_is_safe(client, db_session):
    _seed(db_session)

    first = dict(delete_broken_seed_users(db_session))
    assert all(outcome == "deleted" for outcome in first.values())

    second = dict(delete_broken_seed_users(db_session))
    assert all(outcome == "not_found" for outcome in second.values())


def test_normal_seed_recreates_the_five_deleted_accounts(client, db_session):
    _seed(db_session)
    delete_broken_seed_users(db_session)

    results = dict(_seed(db_session))
    for username in BROKEN_SEED_USERNAMES:
        assert results[username] == "created"
        assert db_session.query(User).filter(User.username == username).first() is not None


def test_recreated_accounts_can_log_in_with_testtest(client, db_session):
    _seed(db_session)
    delete_broken_seed_users(db_session)
    _seed(db_session)

    response = _login_temp(client, "EllieWebb")
    assert response.status_code == 200, response.text
    assert response.json()["user"]["must_change_credentials"] is True


def test_recreated_accounts_have_the_correct_roles(client, db_session):
    _seed(db_session)
    delete_broken_seed_users(db_session)
    _seed(db_session)

    expected_roles = {spec.username: spec.role for spec in TEMPORARY_ACCOUNTS}
    for username in BROKEN_SEED_USERNAMES:
        user = db_session.query(User).filter(User.username == username).first()
        assert user.role == expected_roles[username]


def test_delete_not_found_when_account_never_existed(client, db_session):
    outcomes = dict(delete_broken_seed_users(db_session))
    assert outcomes == {username: "not_found" for username in BROKEN_SEED_USERNAMES}
