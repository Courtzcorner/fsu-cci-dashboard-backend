"""
Fully deletes every OLD dashboard temporary test login and recreates the
CURRENT six testing accounts from scratch, atomically, in one shot.

This is a narrowly scoped, one-time "clean reset" command for the
testing group - it does NOT modify the normal login system, JWT logic,
password hashing, role authorization, CSV import, alumni directory
records, analytics, profile matching, dashboard data, or any existing
permanent user. It shares the exact same account-creation logic and
password-hashing utility (`app.security.hash_password`) that
successfully created the original "courtneystokes" account.

Deliberately NOT run at application startup: this is a one-time,
explicitly-invoked operation, run manually from a Render Shell (or any
other one-off shell against the production database). Because it is
destructive (see below), it REQUIRES an explicit confirmation flag and
refuses to run without it.

OLD temporary logins searched for and deleted - EXACT current username
match only, case insensitive. The `temporary_account_created_at` marker
is explicitly NOT required for this one-time command, so a legacy or
broken row under one of these exact names is replaced regardless of how
it originally got there. Never matched by a similar name, and never
matched by `previous_username` alone - an account that has already
renamed itself away from one of these names is left completely
untouched:

    Eberanderee, EllieWebb, Bellabozied, OwenV, EbeAlum,
    courtneystokes, courtney1

NEW temporary accounts created fresh afterward (a newly generated
password hash for `testtest`, must_change_credentials=True, no Alumni
link):

    Eberanderee   role=admin
    EllieWebb     role=alumni
    Bellabozied   role=alumni
    OwenV         role=alumni
    EbeAlum       role=alumni
    courtney1     role=alumni

The retired "courtneystokes" login is deleted and deliberately NOT
recreated.

See app.seed.temporary_accounts.recreate_dashboard_test_accounts for the
exact rules (never touches unrelated users under any other username,
never touches an account that already completed setup and renamed away,
never touches imported Alumni/CSV/company/university/analytics data, and
rolls back the ENTIRE operation - deletions included - if any of the six
new accounts cannot be created).

Usage (Render Shell, or any environment with DATABASE_URL configured) -
the confirmation flag is REQUIRED:

    python scripts/recreate_dashboard_test_accounts.py --confirm-exact-account-recreation

Safe to run more than once: each run deletes whatever currently exists
under these seven exact usernames and recreates the current six fresh.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.seed.temporary_accounts import OLD_DASHBOARD_TEST_USERNAMES, recreate_dashboard_test_accounts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--confirm-exact-account-recreation",
        action="store_true",
        required=True,
        help="Required. Confirms you understand this permanently deletes any User row whose "
        "CURRENT username exactly matches one of: " + ", ".join(OLD_DASHBOARD_TEST_USERNAMES) + ", "
        "then recreates the current six dashboard test accounts from scratch.",
    )
    args = parser.parse_args()

    if not args.confirm_exact_account_recreation:
        # argparse's required=True already enforces this, but fail loudly
        # and safely regardless if that ever changes.
        print("Refusing to run: --confirm-exact-account-recreation was not passed.")
        return 1

    db = SessionLocal()
    try:
        result = recreate_dashboard_test_accounts(db)
    finally:
        db.close()

    if not result.committed:
        print("\nAccounts deleted: 0")
        print("Accounts created: 0")
        print(f"Accounts failed: {len(result.failed)}")
        for username, error in result.failed:
            print(f"  - {username}: FAILED - {error}")
        print("Transaction committed: false")
        print("\nThe entire operation was rolled back. No accounts were deleted or created.")
        return 1

    deleted_count = sum(1 for _, outcome in result.deleted if outcome == "deleted_old")
    created_count = len(result.created)

    print("\nDeletion results for old temporary logins:")
    for username, outcome in result.deleted:
        print(f"  - {username}: {outcome}")

    print("\nAccounts deleted:", deleted_count)
    print("Accounts created:", created_count)
    print("Accounts failed:", len(result.failed))
    print("Transaction committed: true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
