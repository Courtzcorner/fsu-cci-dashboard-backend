"""
Add or update a login user in the backend-only data/users.csv file.

Passwords are never accepted as a plain CLI argument or logged - you'll be
prompted for them (input is not echoed), and only the bcrypt hash is ever
written to disk.

Usage:
    python scripts/create_user.py --username admin --role admin
    python scripts/create_user.py --username jdoe --role alumni
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.csv_user_store import ALLOWED_ROLES, CsvUserRecord, load_users, save_users, users_csv_path  # noqa: E402
from app.security import hash_password  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Add or update a user in data/users.csv")
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", required=True, choices=sorted(ALLOWED_ROLES))
    args = parser.parse_args()

    password = getpass.getpass(f"Password for '{args.username}': ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.")
        sys.exit(1)
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        sys.exit(1)

    users = load_users()
    action = "Updated" if args.username in users else "Created"
    users[args.username] = CsvUserRecord(
        username=args.username,
        password_hash=hash_password(password),
        role=args.role,
    )
    save_users(users)

    print(f"{action} user '{args.username}' with role '{args.role}' in {users_csv_path()}")


if __name__ == "__main__":
    main()
