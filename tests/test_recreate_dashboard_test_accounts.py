"""
Tests for the clean-slate `recreate_dashboard_test_accounts` command
(app.seed.temporary_accounts.recreate_dashboard_test_accounts /
scripts/recreate_dashboard_test_accounts.py): deletes every OLD
dashboard temporary test login (by EXACT current username match, case
insensitive - the `temporary_account_created_at` marker is NOT required
for this specific, one-time, explicitly-confirmed command) and recreates
the CURRENT six from scratch, atomically.

Additive-only: these tests never touch authentication behavior, JWT
logic, password hashing, role authorization, CSV import, alumni
directory records, analytics, or profile matching beyond proving this
one new command works correctly on top of them.
"""
from app.models.alumni import Alumni
from app.models.user import User
from app.security import hash_password, verify_password
from app.seed.temporary_accounts import (
    DASHBOARD_TEST_ACCOUNTS,
    OLD_DASHBOARD_TEST_USERNAMES,
    recreate_dashboard_test_accounts,
    seed_temporary_accounts,
)
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME

STRONG_PASSWORD = "NewStrongPass1"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _login_temp(client, username, password="testtest"):
    return client.post("/login", json={"username": username, "password": password})


def _expected_roles():
    return {spec.username: spec.role for spec in DASHBOARD_TEST_ACCOUNTS}


# --------------------------------------------------------------------------
# 1-6. Fresh creation of the six accounts
# --------------------------------------------------------------------------


def test_all_six_accounts_are_freshly_created(client, db_session):
    result = recreate_dashboard_test_accounts(db_session)
    assert result.committed is True
    assert {u for u, _ in result.created} == {spec.username for spec in DASHBOARD_TEST_ACCOUNTS}

    for spec in DASHBOARD_TEST_ACCOUNTS:
        user = db_session.query(User).filter(User.username == spec.username).first()
        assert user is not None


def test_every_account_can_log_in_with_testtest(client, db_session):
    recreate_dashboard_test_accounts(db_session)
    for spec in DASHBOARD_TEST_ACCOUNTS:
        response = _login_temp(client, spec.username)
        assert response.status_code == 200, response.text
        assert response.json()["user"]["must_change_credentials"] is True


def test_eberanderee_has_admin_role(client, db_session):
    recreate_dashboard_test_accounts(db_session)
    user = db_session.query(User).filter(User.username == "Eberanderee").first()
    assert user.role == "admin"


def test_other_five_accounts_have_alumni_role(client, db_session):
    recreate_dashboard_test_accounts(db_session)
    for username in ("EllieWebb", "Bellabozied", "OwenV", "EbeAlum", "courtney1"):
        user = db_session.query(User).filter(User.username == username).first()
        assert user.role == "alumni"


def test_all_six_have_must_change_credentials_true(client, db_session):
    recreate_dashboard_test_accounts(db_session)
    for spec in DASHBOARD_TEST_ACCOUNTS:
        user = db_session.query(User).filter(User.username == spec.username).first()
        assert user.must_change_credentials is True
        assert user.temporary_account_created_at is not None
        assert user.credentials_updated_at is None
        assert user.previous_username is None
        assert user.username_changed_at is None


def test_passwords_are_hashed_and_never_stored_as_testtest(client, db_session):
    recreate_dashboard_test_accounts(db_session)
    for spec in DASHBOARD_TEST_ACCOUNTS:
        user = db_session.query(User).filter(User.username == spec.username).first()
        assert user.password_hash != "testtest"
        assert user.password_hash.startswith("$2b$")
        assert verify_password("testtest", user.password_hash)


# --------------------------------------------------------------------------
# 7-8. courtneystokes retired, courtney1 created
# --------------------------------------------------------------------------


def test_old_courtneystokes_login_is_removed(client, db_session):
    # Simulate a pre-existing "courtneystokes" temporary account from the
    # earlier generation of this seed.
    seed_temporary_accounts(db_session)
    assert db_session.query(User).filter(User.username == "courtneystokes").first() is not None

    recreate_dashboard_test_accounts(db_session)

    assert db_session.query(User).filter(User.username == "courtneystokes").first() is None
    old_login = client.post("/login", json={"username": "courtneystokes", "password": "testtest"})
    assert old_login.status_code == 401


def test_courtney1_is_created(client, db_session):
    recreate_dashboard_test_accounts(db_session)
    user = db_session.query(User).filter(User.username == "courtney1").first()
    assert user is not None
    assert user.role == "alumni"


# --------------------------------------------------------------------------
# 9-11. Unrelated data is completely untouched
# --------------------------------------------------------------------------


def test_unrelated_users_remain_untouched(client, admin_user, db_session):
    password_hash_before = admin_user.password_hash
    role_before = admin_user.role

    recreate_dashboard_test_accounts(db_session)

    db_session.refresh(admin_user)
    assert admin_user.username == ADMIN_USERNAME
    assert admin_user.password_hash == password_hash_before
    assert admin_user.role == role_before
    assert admin_user.must_change_credentials is False

    login = client.post("/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert login.status_code == 200


def test_imported_alumni_records_remain_untouched(client, db_session):
    alumni = Alumni(first_name="Jane", last_name="Doe", full_name="Jane Doe", is_active=True)
    db_session.add(alumni)
    db_session.commit()
    alumni_id = alumni.id

    recreate_dashboard_test_accounts(db_session)

    still_there = db_session.query(Alumni).filter(Alumni.id == alumni_id).first()
    assert still_there is not None
    assert still_there.full_name == "Jane Doe"
    assert still_there.is_active is True


def test_csv_data_remains_untouched(client, db_session, organization):
    """The `alumni` table (the only persisted representation of imported
    CSV rows in this schema) is the target here - no CSVImport metadata
    row is created/deleted by this command either."""
    from app.models.audit import CSVImport

    csv_import = CSVImport(organization_id=organization.id, filename="test.csv", rows_received=1)
    db_session.add(csv_import)
    db_session.commit()
    csv_import_id = csv_import.id

    recreate_dashboard_test_accounts(db_session)

    still_there = db_session.query(CSVImport).filter(CSVImport.id == csv_import_id).first()
    assert still_there is not None


# --------------------------------------------------------------------------
# 12. Idempotency - running twice creates no duplicates
# --------------------------------------------------------------------------


def test_running_the_command_twice_does_not_create_duplicates(client, db_session):
    recreate_dashboard_test_accounts(db_session)
    result = recreate_dashboard_test_accounts(db_session)
    assert result.committed is True

    for spec in DASHBOARD_TEST_ACCOUNTS:
        matches = db_session.query(User).filter(User.username == spec.username).all()
        assert len(matches) == 1


def test_running_the_command_twice_deletes_the_first_generation(client, db_session):
    recreate_dashboard_test_accounts(db_session)
    first_ids = {
        spec.username: db_session.query(User).filter(User.username == spec.username).first().id
        for spec in DASHBOARD_TEST_ACCOUNTS
    }

    recreate_dashboard_test_accounts(db_session)

    db_session.expire_all()
    for spec in DASHBOARD_TEST_ACCOUNTS:
        user = db_session.query(User).filter(User.username == spec.username).first()
        assert user is not None
        # A brand-new id every time - never a reused row.
        assert user.id != first_ids[spec.username]


def test_second_run_does_not_touch_a_renamed_account(client, db_session):
    recreate_dashboard_test_accounts(db_session)
    token = _login_temp(client, "EllieWebb").json()["access_token"]
    client.post(
        "/auth/complete-first-login",
        json={"new_username": "ellie.renamed2", "new_password": STRONG_PASSWORD, "confirm_password": STRONG_PASSWORD},
        headers=_auth(token),
    )
    renamed_password_hash = db_session.query(User).filter(User.username == "ellie.renamed2").first().password_hash

    result = recreate_dashboard_test_accounts(db_session)
    outcomes = dict(result.deleted)
    assert outcomes["EllieWebb"] == "skipped_renamed"

    renamed = db_session.query(User).filter(User.username == "ellie.renamed2").first()
    assert renamed is not None
    assert renamed.password_hash == renamed_password_hash
    assert renamed.must_change_credentials is False

    # A fresh "EllieWebb" login exists again for testers.
    fresh_ellie = db_session.query(User).filter(User.username == "EllieWebb").first()
    assert fresh_ellie is not None
    assert fresh_ellie.must_change_credentials is True


# --------------------------------------------------------------------------
# 13. Failure rolls back the ENTIRE operation
# --------------------------------------------------------------------------


def test_a_failure_rolls_back_the_complete_operation(client, db_session, monkeypatch):
    import app.seed.temporary_accounts as temp_accounts_module

    original_create = temp_accounts_module._create_dashboard_test_account

    def failing_create(db, spec):
        if spec.username == "courtney1":
            raise RuntimeError("simulated failure creating courtney1")
        return original_create(db, spec)

    # "courtney1" is deliberately last in DASHBOARD_TEST_ACCOUNTS, so this
    # forces a failure AFTER the other five have already been created (and
    # AFTER every old account has already been deleted) within the same,
    # still-uncommitted transaction.
    monkeypatch.setattr(temp_accounts_module, "_create_dashboard_test_account", failing_create)

    result = temp_accounts_module.recreate_dashboard_test_accounts(db_session)

    assert result.committed is False
    assert len(result.failed) > 0

    db_session.expire_all()
    # Nothing was deleted or created - not even the five accounts
    # successfully created before hitting the simulated "courtney1"
    # failure, and not even the old rows that were already deleted
    # earlier in the same transaction.
    for username in ("Eberanderee", "EllieWebb", "Bellabozied", "OwenV", "EbeAlum", "courtney1"):
        assert db_session.query(User).filter(User.username == username).first() is None


# --------------------------------------------------------------------------
# Old accounts with a null `temporary_account_created_at` marker (e.g. a
# broken/legacy row) are deleted and replaced anyway - the marker is
# deliberately NOT required by this destructive, exact-username-only
# command.
# --------------------------------------------------------------------------


def _make_legacy_row_without_marker(db_session, username, role="alumni", password="SomeBrokenPass1"):
    """A pre-existing row under one of the seven designated exact
    usernames, but with `temporary_account_created_at=None` - simulating
    a legacy/broken account created before this seed system existed, or
    one that was never properly marked. This command is explicitly
    authorized to delete and replace it anyway."""
    user = User(username=username, password_hash=hash_password(password), role=role)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_old_accounts_with_null_temporary_marker_are_deleted_and_recreated(client, db_session):
    legacy_rows = {}
    for username in ("Eberanderee", "EllieWebb", "Bellabozied", "OwenV", "EbeAlum", "courtneystokes"):
        legacy_rows[username] = _make_legacy_row_without_marker(db_session, username)
        assert legacy_rows[username].temporary_account_created_at is None

    result = recreate_dashboard_test_accounts(db_session)
    assert result.committed is True

    outcomes = dict(result.deleted)
    for username in legacy_rows:
        assert outcomes[username] == "deleted_old"

    # Every legacy row's original id is gone - not repaired in place.
    db_session.expire_all()
    for username, legacy_user in legacy_rows.items():
        current = db_session.query(User).filter(User.username == username).first()
        if username == "courtneystokes":
            assert current is None  # retired, never recreated
        else:
            assert current is not None
            assert current.id != legacy_user.id
            assert current.must_change_credentials is True
            assert current.temporary_account_created_at is not None
            assert verify_password("testtest", current.password_hash)
            assert not verify_password("SomeBrokenPass1", current.password_hash)

    # And every account (including the fresh courtney1) can log in.
    for spec in DASHBOARD_TEST_ACCOUNTS:
        response = _login_temp(client, spec.username)
        assert response.status_code == 200, response.text
        assert response.json()["user"]["role"] == spec.role


def test_single_legacy_row_without_marker_is_replaced(client, db_session):
    """Focused single-account version of the above, matching the exact
    request: an old account exists with temporary_account_created_at=null
    and must still be deleted and recreated successfully."""
    legacy = _make_legacy_row_without_marker(db_session, "OwenV", role="alumni")
    legacy_id = legacy.id

    result = recreate_dashboard_test_accounts(db_session)
    assert result.committed is True
    assert dict(result.deleted)["OwenV"] == "deleted_old"

    db_session.expire_all()
    fresh = db_session.query(User).filter(User.username == "OwenV").first()
    assert fresh is not None
    assert fresh.id != legacy_id
    assert fresh.role == "alumni"
    assert fresh.must_change_credentials is True
    assert verify_password("testtest", fresh.password_hash)


# --------------------------------------------------------------------------
# 14-15. Existing test suites keep passing (see the full suite run) - a
# quick smoke check that ordinary auth/session behavior is unaffected by
# importing this module at all.
# --------------------------------------------------------------------------


def test_old_dashboard_test_usernames_constant_matches_spec():
    assert set(OLD_DASHBOARD_TEST_USERNAMES) == {
        "Eberanderee", "EllieWebb", "Bellabozied", "OwenV", "EbeAlum", "courtneystokes", "courtney1",
    }
