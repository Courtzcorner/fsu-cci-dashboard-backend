"""
Create (or update) a login user in the shared database.

Passwords are never accepted as a plain CLI argument or logged - you'll be
prompted for them (input is not echoed), and only the bcrypt hash is ever
written to the `users` table.

Usage:
    python scripts/create_user.py --username admin --role admin
    python scripts/create_user.py --username jdoe --role alumni --alumni-id <alumni-uuid>
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models.alumni import Alumni  # noqa: E402
from app.models.roles import UserRole  # noqa: E402
from app.models.user import User  # noqa: E402
from app.security import hash_password  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Create/update a user in the shared database.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", required=True, choices=[r.value for r in UserRole])
    parser.add_argument(
        "--alumni-id", default=None, help="Link this login account to an existing alumni record's id"
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.alumni_id:
            alumni = db.get(Alumni, args.alumni_id)
            if alumni is None:
                print(f"No alumni record found with id '{args.alumni_id}'")
                sys.exit(1)
            existing_link = db.query(User).filter(User.alumni_id == args.alumni_id).first()
            if existing_link and existing_link.username != args.username:
                print(f"Alumni record is already linked to user '{existing_link.username}'")
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
            user = User(
                username=args.username,
                password_hash=hash_password(password),
                role=args.role,
                alumni_id=args.alumni_id,
            )
            db.add(user)
            action = "Created"
        else:
            user.password_hash = hash_password(password)
            user.role = args.role
            if args.alumni_id:
                user.alumni_id = args.alumni_id
            action = "Updated"

        db.commit()
        print(f"{action} user '{args.username}' with role '{args.role}'" + (f" linked to alumni {args.alumni_id}" if args.alumni_id else ""))
    finally:
        db.close()


if __name__ == "__main__":
    main()
