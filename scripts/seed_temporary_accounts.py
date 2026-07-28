"""
Provision the seeded temporary testing accounts (username `testtest`
password, forced credential setup on first login). Safe to run more than
once - see app.seed.temporary_accounts.seed_temporary_accounts for the
exact idempotency rules.

Deliberately NOT run at application startup: this is a one-time,
explicitly-invoked operation, run manually from a Render Shell (or any
other one-off shell against the production database).

Usage (Render Shell, or any environment with DATABASE_URL configured):

    python scripts/seed_temporary_accounts.py

To re-issue a fresh `testtest` password for any account that still has
NOT completed first-login setup (never touches an account that already
has - see the module docstring):

    python scripts/seed_temporary_accounts.py --force-reset

To narrowly REPAIR the six exact seed usernames (Eberanderee, EllieWebb,
Bellabozied, OwenV, EbeAlum, courtneystokes) when a pre-existing `User`
row with one of those exact usernames was silently skipped by the normal
seed above - e.g. the "Invalid username or password" symptom in
production because that row never received the hashed temporary
password / must_change_credentials=True. See
app.seed.temporary_accounts.repair_seed_usernames for the exact,
narrowly-scoped rules (never touches any other user, never creates a
duplicate, never touches an account that already completed setup and
renamed away):

    python scripts/seed_temporary_accounts.py --repair-seed-usernames

--repair-seed-usernames is mutually exclusive with --force-reset and
takes priority if both are passed.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.seed.temporary_accounts import repair_seed_usernames, seed_temporary_accounts  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force-reset",
        action="store_true",
        help="Re-issue the temporary password for any configured account that has NOT yet "
        "completed first-login setup. Never touches an account that already has.",
    )
    parser.add_argument(
        "--repair-seed-usernames",
        action="store_true",
        help="Narrowly repair the exact six seed usernames if a pre-existing row with one of "
        "those usernames was skipped by the normal seed (missing hashed temporary password / "
        "must_change_credentials). Never creates a duplicate, never touches any other user, "
        "never touches an account that already completed setup and renamed away.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.repair_seed_usernames:
            results = repair_seed_usernames(db)
            print("Temporary account repair results:")
        else:
            results = seed_temporary_accounts(db, force_reset=args.force_reset)
            print("Temporary account seed results:")
        for username, outcome in results:
            print(f"  - {username}: {outcome}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
