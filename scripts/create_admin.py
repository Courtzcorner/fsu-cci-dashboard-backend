"""
Create (or update) a user and grant them a role on an organization.

Passwords are hashed with bcrypt and are only ever collected via a
non-echoing prompt - never pass them as a plain CLI argument.

Usage:
    python scripts/create_admin.py --username admin --organization fsu-cci --role organization_admin
    python scripts/create_admin.py --username root --role super_admin --organization fsu-cci
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.roles import UserRole  # noqa: E402
from app.models.user import User, UserOrganizationRole  # noqa: E402
from app.security import hash_password  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Create/update a user and assign an organization role.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--organization", required=True, help="Organization slug (e.g. fsu-cci)")
    parser.add_argument(
        "--role", required=True, choices=[r.value for r in UserRole], help="Role to grant on this organization"
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        organization = db.query(Organization).filter(Organization.slug == args.organization).first()
        if organization is None:
            print(
                f"No organization found with slug '{args.organization}'. "
                "Run scripts/seed_organizations.py first."
            )
            sys.exit(1)

        password = getpass.getpass(f"Password for '{args.username}': ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.")
            sys.exit(1)
        if len(password) < 8:
            print("Password must be at least 8 characters.")
            sys.exit(1)

        user = db.query(User).filter(User.username == args.username).first()
        if user is None:
            user = User(username=args.username, password_hash=hash_password(password))
            db.add(user)
            db.flush()
        else:
            user.password_hash = hash_password(password)

        existing_role = (
            db.query(UserOrganizationRole)
            .filter(
                UserOrganizationRole.user_id == user.id,
                UserOrganizationRole.organization_id == organization.id,
            )
            .first()
        )
        if existing_role:
            existing_role.role = args.role
        else:
            db.add(
                UserOrganizationRole(
                    user_id=user.id, organization_id=organization.id, role=args.role
                )
            )

        db.commit()
        print(f"User '{args.username}' now has role '{args.role}' on organization '{organization.slug}'.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
