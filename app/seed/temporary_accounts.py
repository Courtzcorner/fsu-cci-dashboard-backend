"""
Idempotent provisioning for the small set of seeded temporary accounts
(testing group). Safe to run more than once:

  - A username that doesn't exist yet (and was never renamed away from,
    see `previous_username` check) is created fresh.
  - A temporary account that still has `must_change_credentials=True` is
    left completely unchanged UNLESS `force_reset=True` is explicitly
    passed - even then, only its password/token_version are reset, never
    its role or id.
  - An account that has already completed first-login setup - whether
    still under its original temporary username, or found via
    `previous_username` after being renamed - is NEVER modified,
    regardless of `force_reset`.

Never invoked automatically at application startup (see
scripts/seed_temporary_accounts.py for the one-time Render Shell
command) - only ever run deliberately, on demand.

See `recreate_dashboard_test_accounts` at the bottom of this module for a
separate, more aggressive "delete every old temporary test login and
recreate the current six from scratch" command - it deliberately does
NOT try to preserve/repair any pre-existing row (unlike
`repair_seed_usernames` above), since its entire purpose is a clean
reset of the testing group.
"""
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.mixins import utcnow
from app.models.user import User
from app.models.user_profile import ProfileMatchCandidate, UserEducationHistory, UserProfile, UserWorkHistory
from app.security import hash_password, verify_password
from app.services.audit_service import record_audit_log
from app.services.credential_setup_service import TEMPORARY_PASSWORD

SeedOutcome = Literal["created", "reset", "unchanged", "already_completed"]
RepairOutcome = Literal[
    "created", "repaired", "already_pending", "completed_and_renamed", "role_conflict", "not_found"
]
DeleteOutcome = Literal["deleted", "not_found", "skipped_renamed"]
OldAccountOutcome = Literal["deleted_old", "not_found_old", "skipped_renamed"]
NewAccountOutcome = Literal["created_new", "failed"]

# The five ORIGINAL broken temporary seed usernames - deliberately does
# NOT include "courtneystokes" (added later, never reported broken) or
# any other account.
BROKEN_SEED_USERNAMES: tuple[str, ...] = ("Eberanderee", "EllieWebb", "Bellabozied", "OwenV", "EbeAlum")

# Every username the dashboard testing group has EVER used, across every
# generation of this seed (including "courtneystokes", replaced below by
# "courtney1", and "courtney1" itself so that re-running the recreate
# command is idempotent - see `recreate_dashboard_test_accounts`).
OLD_DASHBOARD_TEST_USERNAMES: tuple[str, ...] = (
    "Eberanderee", "EllieWebb", "Bellabozied", "OwenV", "EbeAlum", "courtneystokes", "courtney1",
)


@dataclass(frozen=True)
class TemporaryAccountSpec:
    username: str
    role: str


TEMPORARY_ACCOUNTS: list[TemporaryAccountSpec] = [
    TemporaryAccountSpec(username="Eberanderee", role="admin"),
    TemporaryAccountSpec(username="EllieWebb", role="alumni"),
    TemporaryAccountSpec(username="Bellabozied", role="alumni"),
    TemporaryAccountSpec(username="OwenV", role="alumni"),
    TemporaryAccountSpec(username="EbeAlum", role="alumni"),
    TemporaryAccountSpec(username="courtneystokes", role="alumni"),
    # --- Additive second batch - same seed/idempotency rules apply ---
    TemporaryAccountSpec(username="Bellab", role="alumni"),
    TemporaryAccountSpec(username="Owebva", role="alumni"),
    TemporaryAccountSpec(username="Ellliew", role="alumni"),
    TemporaryAccountSpec(username="JuliaS", role="alumni"),
    TemporaryAccountSpec(username="CourtneyS", role="admin"),
    # --- Additive third batch - same seed/idempotency rules apply ---
    TemporaryAccountSpec(username="melissar", role="admin"),
    TemporaryAccountSpec(username="amelissa", role="alumni"),
    # --- Additive fourth batch - same seed/idempotency rules apply ---
    TemporaryAccountSpec(username="EbeR", role="admin"),
    TemporaryAccountSpec(username="EbeRan", role="alumni"),
]

# The exact six seed usernames the --repair-seed-usernames CLI flag is
# scoped to. Kept as its own constant (rather than re-deriving it inline
# every call) so `repair_seed_usernames` below is unambiguously "these
# six exact names, case insensitively" - never any other user in the
# `users` table.
REPAIR_SEED_USERNAMES: tuple[str, ...] = tuple(spec.username for spec in TEMPORARY_ACCOUNTS)

# The CURRENT six dashboard testing accounts - source of truth for
# `recreate_dashboard_test_accounts`. "courtney1" replaces the retired
# "courtneystokes" username. Roles here are authoritative and are never
# inferred from any deleted row.
DASHBOARD_TEST_ACCOUNTS: list[TemporaryAccountSpec] = [
    TemporaryAccountSpec(username="Eberanderee", role="admin"),
    TemporaryAccountSpec(username="EllieWebb", role="alumni"),
    TemporaryAccountSpec(username="Bellabozied", role="alumni"),
    TemporaryAccountSpec(username="OwenV", role="alumni"),
    TemporaryAccountSpec(username="EbeAlum", role="alumni"),
    TemporaryAccountSpec(username="courtney1", role="alumni"),
]


def seed_temporary_accounts(db: Session, force_reset: bool = False) -> list[tuple[str, SeedOutcome]]:
    results: list[tuple[str, SeedOutcome]] = []

    for spec in TEMPORARY_ACCOUNTS:
        existing = db.query(User).filter(func.lower(User.username) == spec.username.lower()).first()

        if existing is not None:
            if existing.must_change_credentials:
                if force_reset:
                    existing.password_hash = hash_password(TEMPORARY_PASSWORD)
                    existing.token_version = (existing.token_version or 0) + 1
                    results.append((spec.username, "reset"))
                else:
                    results.append((spec.username, "unchanged"))
            else:
                # Already completed setup, still under this username.
                results.append((spec.username, "already_completed"))
            continue

        # No user currently has this username - but if someone previously
        # renamed AWAY from it after completing setup, this username is
        # "retired" and must never be silently recreated as a brand-new
        # temporary account.
        previously_renamed = (
            db.query(User).filter(func.lower(User.previous_username) == spec.username.lower()).first()
        )
        if previously_renamed is not None:
            results.append((spec.username, "already_completed"))
            continue

        user = User(
            username=spec.username,
            password_hash=hash_password(TEMPORARY_PASSWORD),
            role=spec.role,
            must_change_credentials=True,
            temporary_account_created_at=utcnow(),
        )
        db.add(user)
        db.flush()
        record_audit_log(
            db,
            user_id=None,
            action="temporary_account_created",
            entity_type="user",
            entity_id=user.id,
            details={"username": spec.username, "role": spec.role},
        )
        results.append((spec.username, "created"))

    db.commit()
    return results


def repair_seed_usernames(db: Session) -> list[tuple[str, RepairOutcome]]:
    """Narrowly scoped production repair for exactly the six seed
    usernames in REPAIR_SEED_USERNAMES (case insensitive) - never any
    other row in `users`.

    This exists because a `User` row matching one of these exact
    usernames may have already existed BEFORE the temporary-account seed
    feature shipped (e.g. a manually created production account). The
    plain, safe `seed_temporary_accounts()` above intentionally never
    touches an existing row with `must_change_credentials=False`,
    because it can't tell that situation apart from "already completed
    first-login setup" - so that pre-existing row was silently skipped,
    never given the hashed `testtest` password or `must_change_credentials
    =True`, which is exactly the "Invalid username or password" symptom
    in production.

    This function CAN tell the difference, because a row that completed
    setup via POST /auth/complete-first-login always renames itself away
    from its original temporary username (see
    app.services.credential_setup_service.validate_new_username, which
    rejects reusing the current username). So: if a row's CURRENT
    username still exactly matches one of these six seed names, it is
    guaranteed to have never completed setup - it is always safe to
    inspect and, if needed, repair.

    For each of the six usernames, exactly one of the following is
    performed and reported:
      - "created": no row exists under this username OR its
        `previous_username` - created fresh, identical to
        `seed_temporary_accounts()`'s normal creation path.
      - "repaired": a row exists under this exact username, with the
        correct role, but is missing the hashed temporary password /
        `must_change_credentials=True` / `temporary_account_created_at`
        - fixed in place. The row's id, role, and every user_id-keyed
        relationship (UserProfile, alumni_id link, etc.) are completely
        untouched; only credential-state columns are written.
      - "already_pending": a row exists under this exact username and is
        already fully correct - no changes made.
      - "completed_and_renamed": no row exists under this username, but
        a row's `previous_username` matches it - that account already
        completed setup and renamed away; never touched or recreated.
      - "role_conflict": a row exists under this exact username but with
        a DIFFERENT role than this seed configuration expects - never
        touched, flagged for manual review rather than silently
        overwriting a role.
      - "not_found": reserved/never produced by the current
        implementation (a wholly missing account is instead reported as
        "created" - see above) - kept in the result type for
        completeness.
    """
    results: list[tuple[str, RepairOutcome]] = []

    for spec in TEMPORARY_ACCOUNTS:
        user = db.query(User).filter(func.lower(User.username) == spec.username.lower()).first()

        if user is None:
            renamed = (
                db.query(User).filter(func.lower(User.previous_username) == spec.username.lower()).first()
            )
            if renamed is not None:
                results.append((spec.username, "completed_and_renamed"))
                continue

            new_user = User(
                username=spec.username,
                password_hash=hash_password(TEMPORARY_PASSWORD),
                role=spec.role,
                must_change_credentials=True,
                temporary_account_created_at=utcnow(),
            )
            db.add(new_user)
            db.flush()
            record_audit_log(
                db,
                user_id=None,
                action="temporary_account_created",
                entity_type="user",
                entity_id=new_user.id,
                details={"username": spec.username, "role": spec.role},
            )
            results.append((spec.username, "created"))
            continue

        # Found under its exact current seed username - by construction
        # (see docstring above) this account has never completed
        # first-login setup, so it is always safe to inspect here.
        if user.role != spec.role:
            results.append((spec.username, "role_conflict"))
            continue

        already_correct = (
            user.must_change_credentials is True
            and user.temporary_account_created_at is not None
            and verify_password(TEMPORARY_PASSWORD, user.password_hash)
        )
        if already_correct:
            results.append((spec.username, "already_pending"))
            continue

        # Preserves user.id and user.role - only credential-state columns
        # are written.
        user.password_hash = hash_password(TEMPORARY_PASSWORD)
        user.must_change_credentials = True
        if user.temporary_account_created_at is None:
            user.temporary_account_created_at = utcnow()
        user.token_version = (user.token_version or 0) + 1
        record_audit_log(
            db,
            user_id=user.id,
            action="temporary_account_repaired",
            entity_type="user",
            entity_id=user.id,
            details={"username": user.username, "role": user.role},
        )
        results.append((spec.username, "repaired"))

    db.commit()
    return results


def delete_broken_seed_users(db: Session) -> list[tuple[str, DeleteOutcome]]:
    """Permanently deletes ONLY the five original broken temporary seed
    accounts (BROKEN_SEED_USERNAMES) so they can be recreated cleanly by
    `seed_temporary_accounts()`. Deliberately excludes "courtneystokes"
    and every other user in the system.

    Matching is by CURRENT username only (case insensitive) - an account
    is never deleted based on `previous_username` alone, and an account
    that has already renamed itself away from one of these five names is
    left completely untouched (reported as "skipped_renamed", not
    deleted, not recreated).

    Dependent rows are cleaned up ONLY as required by the `users` table's
    foreign keys - each of these broken accounts predates the
    first-login flow, so in practice none of them ever has a UserProfile
    row, but this is handled defensively (and explicitly, rather than
    relying on the database's ON DELETE CASCADE configuration) in case
    one was created:
      - UserProfile (if any) for that user_id, plus its child
        UserWorkHistory / UserEducationHistory / ProfileMatchCandidate
        rows.
    Nothing else is touched: `alumni` (imported CSV directory data),
    `csv_imports`, `audit_logs`, and every other user's own
    UserProfile/relationships are completely unaffected. The whole batch
    is committed atomically - either every matched account (and its
    dependents) is deleted, or (on error) none of them are.
    """
    results: list[tuple[str, DeleteOutcome]] = []
    to_delete: list[User] = []

    for username in BROKEN_SEED_USERNAMES:
        user = db.query(User).filter(func.lower(User.username) == username.lower()).first()

        if user is not None:
            print(f"Will delete: {user.username} (id={user.id}, role={user.role})")
            to_delete.append(user)
            results.append((username, "deleted"))
            continue

        # Never deleted, and never recreated by this command - an account
        # that has already completed setup and renamed away from this
        # username is out of scope entirely.
        renamed = db.query(User).filter(func.lower(User.previous_username) == username.lower()).first()
        if renamed is not None:
            results.append((username, "skipped_renamed"))
        else:
            results.append((username, "not_found"))

    for user in to_delete:
        profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
        if profile is not None:
            db.query(ProfileMatchCandidate).filter(ProfileMatchCandidate.user_profile_id == profile.id).delete(
                synchronize_session=False
            )
            db.query(UserWorkHistory).filter(UserWorkHistory.user_profile_id == profile.id).delete(
                synchronize_session=False
            )
            db.query(UserEducationHistory).filter(UserEducationHistory.user_profile_id == profile.id).delete(
                synchronize_session=False
            )
            db.delete(profile)

        # user_id=None (not the row about to be deleted) - AuditLog.user_id
        # is a nullable FK to users.id; entity_id is a plain string column
        # (not an FK), so it safely retains the deleted user's id for the
        # audit trail.
        record_audit_log(
            db,
            user_id=None,
            action="temporary_account_deleted",
            entity_type="user",
            entity_id=user.id,
            details={"username": user.username, "role": user.role},
        )
        db.delete(user)

    db.commit()
    return results


def _create_dashboard_test_account(db: Session, spec: TemporaryAccountSpec) -> User:
    """The exact same account-creation logic and password-hashing utility
    used by `seed_temporary_accounts()` above (the same one that created
    the original "courtneystokes" account) - a brand-new row, a fresh
    generated id (never reused from a deleted row), `testtest` hashed
    with the existing bcrypt utility (never stored in plaintext), and
    every credential-state column reset to its true "never logged in
    yet" starting value."""
    user = User(
        username=spec.username,
        password_hash=hash_password(TEMPORARY_PASSWORD),
        role=spec.role,
        must_change_credentials=True,
        temporary_account_created_at=utcnow(),
        credentials_updated_at=None,
        previous_username=None,
        username_changed_at=None,
        token_version=0,
    )
    db.add(user)
    db.flush()
    record_audit_log(
        db,
        user_id=None,
        action="temporary_account_created",
        entity_type="user",
        entity_id=user.id,
        details={"username": spec.username, "role": spec.role},
    )
    return user


@dataclass
class RecreateDashboardTestAccountsResult:
    deleted: list[tuple[str, OldAccountOutcome]]
    created: list[tuple[str, NewAccountOutcome]]
    failed: list[tuple[str, str]]
    committed: bool


def recreate_dashboard_test_accounts(db: Session) -> RecreateDashboardTestAccountsResult:
    """Fully deletes every OLD dashboard temporary test login
    (OLD_DASHBOARD_TEST_USERNAMES) and recreates the CURRENT six
    (DASHBOARD_TEST_ACCOUNTS) from scratch, atomically.

    Deliberately more aggressive than `seed_temporary_accounts` /
    `repair_seed_usernames` above - it does not try to detect or
    preserve a pre-existing row; its entire purpose is a clean, one-time
    reset of this specific, hard-coded testing group. Callers (see
    scripts/recreate_dashboard_test_accounts.py) are expected to gate
    this behind an explicit CLI confirmation flag, since - unlike the
    marker-gated `delete_broken_seed_users` above - this function WILL
    delete a row matching one of these seven exact usernames even if it
    was never created by this seed system.

      - IDENTIFICATION (exact-match only): a `User` row is a candidate
        for deletion if, and ONLY if, its CURRENT username (case
        insensitive) exactly matches one of the seven names in
        OLD_DASHBOARD_TEST_USERNAMES. The `temporary_account_created_at`
        marker is deliberately NOT required - this command is explicitly
        authorized to replace a legacy/broken row under one of these
        exact names regardless of how it originally got there. A row is
        NEVER matched by `previous_username` alone, and NEVER matched by
        a "similar" (fuzzy) name - only an exact, case-insensitive match
        on one of these seven literal strings.
      - An account that already completed first-login setup and renamed
        itself away from one of these names is left completely
        untouched (reported "skipped_renamed") - not deleted, and its
        replacement is still freshly created under the original
        temporary username so testers keep a working login.
      - Dependent UserProfile (and its UserWorkHistory /
        UserEducationHistory / ProfileMatchCandidate children) are
        deleted first, ONLY for a row actually being deleted, and ONLY
        because the `users` table foreign key requires it.
      - Never touches `alumni` (imported CSV directory data),
        `csv_imports`, `companies`, universities, analytics data, or any
        other user's own profile/relationships.
      - RECREATION uses the exact same helper as the normal seed
        (`_create_dashboard_test_account`, sharing
        `hash_password`/`TEMPORARY_PASSWORD`) - a brand-new id, a newly
        generated password hash for `testtest` (never reused from a
        deleted row), `must_change_credentials=True`,
        `temporary_account_created_at=now`, and
        `credentials_updated_at` / `previous_username` /
        `username_changed_at` all reset to None.
      - ATOMIC: everything (every deletion AND every creation) happens
        in one transaction. If any of the six new accounts cannot be
        created, the entire transaction - deletions included - is
        rolled back, and NONE of the six new accounts are left partially
        created.
    """
    deleted: list[tuple[str, OldAccountOutcome]] = []
    to_delete: list[User] = []

    for username in OLD_DASHBOARD_TEST_USERNAMES:
        user = db.query(User).filter(func.lower(User.username) == username.lower()).first()

        if user is None:
            renamed = db.query(User).filter(func.lower(User.previous_username) == username.lower()).first()
            deleted.append((username, "skipped_renamed" if renamed is not None else "not_found_old"))
            continue

        to_delete.append(user)

    try:
        for user in to_delete:
            profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
            if profile is not None:
                db.query(ProfileMatchCandidate).filter(ProfileMatchCandidate.user_profile_id == profile.id).delete(
                    synchronize_session=False
                )
                db.query(UserWorkHistory).filter(UserWorkHistory.user_profile_id == profile.id).delete(
                    synchronize_session=False
                )
                db.query(UserEducationHistory).filter(UserEducationHistory.user_profile_id == profile.id).delete(
                    synchronize_session=False
                )
                db.delete(profile)

            record_audit_log(
                db,
                user_id=None,
                action="temporary_account_deleted",
                entity_type="user",
                entity_id=user.id,
                details={"username": user.username, "role": user.role},
            )
            print(f"Deleted old temporary account: {user.username}")
            deleted.append((user.username, "deleted_old"))
            db.delete(user)
        db.flush()

        created: list[tuple[str, NewAccountOutcome]] = []
        for spec in DASHBOARD_TEST_ACCOUNTS:
            conflict = db.query(User).filter(func.lower(User.username) == spec.username.lower()).first()
            if conflict is not None:
                raise RuntimeError(
                    f"Cannot create temporary account '{spec.username}': a User row with that "
                    f"username still exists (id={conflict.id})."
                )
            _create_dashboard_test_account(db, spec)
            db.flush()
            print(f"Created temporary account: {spec.username}")
            print(f"Role: {spec.role}")
            print("Credential setup required: true")
            created.append((spec.username, "created_new"))

    except Exception as exc:
        # Atomic: on ANY failure, the entire transaction - every deletion
        # AND every creation attempted so far - is rolled back, so the
        # database ends up completely unchanged rather than a partial
        # set of the six accounts.
        db.rollback()
        failed = [(spec.username, str(exc)) for spec in DASHBOARD_TEST_ACCOUNTS]
        return RecreateDashboardTestAccountsResult(deleted=[], created=[], failed=failed, committed=False)

    db.commit()
    return RecreateDashboardTestAccountsResult(deleted=deleted, created=created, failed=[], committed=True)
