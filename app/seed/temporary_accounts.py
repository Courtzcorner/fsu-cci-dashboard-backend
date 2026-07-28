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
from app.services.credential_setup_service import TEMPORARY_PASSWORD

SeedOutcome = Literal["created", "reset", "unchanged", "already_completed"]


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
