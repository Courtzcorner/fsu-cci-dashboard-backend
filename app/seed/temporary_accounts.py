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
"""
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.mixins import utcnow
from app.models.user import User
from app.security import hash_password
from app.services.audit_service import record_audit_log
from app.security import verify_password
from app.services.credential_setup_service import TEMPORARY_PASSWORD

SeedOutcome = Literal["created", "reset", "unchanged", "already_completed"]
RepairOutcome = Literal[
    "created", "repaired", "already_pending", "completed_and_renamed", "role_conflict", "not_found"
]


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
]

# The exact six seed usernames the --repair-seed-usernames CLI flag is
# scoped to. Kept as its own constant (rather than re-deriving it inline
# every call) so `repair_seed_usernames` below is unambiguously "these
# six exact names, case insensitively" - never any other user in the
# `users` table.
REPAIR_SEED_USERNAMES: tuple[str, ...] = tuple(spec.username for spec in TEMPORARY_ACCOUNTS)


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
