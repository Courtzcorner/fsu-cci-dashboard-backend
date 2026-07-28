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
from app.models.user import User
from app.seed.temporary_accounts import TEMPORARY_ACCOUNTS, seed_temporary_accounts
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
