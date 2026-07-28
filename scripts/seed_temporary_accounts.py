"""
Provision the five seeded temporary testing accounts (username `testtest`
password, forced credential setup on first login). Safe to run more than
once - see app.seed.temporary_accounts.seed_temporary_accounts for the
exact idempotency rules.

Deliberately NOT run at application startup: this is a one-time,
explicitly-invoked operation, run manually from a Render Shell (or any
other one-off shell against the production database).

Usage (Render Shell, or any environment with DATABASE_URL configured):

    python scripts/seed_temporary_accounts.py

To re-issue a fresh `testtest` password for any of the five accounts
that still have NOT completed first-login setup (never touches an
account that already has - see the module docstring):

    python scripts/seed_temporary_accounts.py --force-reset
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.seed.temporary_accounts import seed_temporary_accounts  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force-reset",
        action="store_true",
        help="Re-issue the temporary password for any of the five accounts that have NOT yet "
        "completed first-login setup. Never touches an account that already has.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        results = seed_temporary_accounts(db, force_reset=args.force_reset)
        print("Temporary account seed results:")
        for username, outcome in results:
            print(f"  - {username}: {outcome}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
