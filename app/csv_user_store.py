"""
Read/write access to the backend-only credentials CSV file
(`data/users.csv`).

This file is the source of truth for login credentials. It is only ever
read/written server-side (here and in `scripts/create_user.py`) - no API
route serves its contents, and nothing in the JWT payload or any response
body echoes the file path, the raw CSV, or a password hash.

Expected columns (header row required): username,password_hash,role
"""
import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["username", "password_hash", "role"]
ALLOWED_ROLES = {"admin", "alumni"}


@dataclass(frozen=True)
class CsvUserRecord:
    username: str
    password_hash: str
    role: str


def users_csv_path() -> Path:
    return get_settings().users_csv_full_path


def load_users() -> Dict[str, CsvUserRecord]:
    """Load all users from the CSV into a dict keyed by username.

    Returns an empty dict (and logs a warning/error) rather than raising,
    so a misconfigured deployment fails logins safely instead of crashing
    the whole app.
    """
    path = users_csv_path()
    if not path.exists():
        logger.warning("Users CSV not found at configured path")
        return {}

    users: Dict[str, CsvUserRecord] = {}
    with path.open("r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None or not set(REQUIRED_COLUMNS).issubset(set(reader.fieldnames)):
            logger.error("Users CSV is missing required columns %s", REQUIRED_COLUMNS)
            return {}

        for row in reader:
            username = (row.get("username") or "").strip()
            password_hash = (row.get("password_hash") or "").strip()
            role = (row.get("role") or "").strip().lower()

            if not username or not password_hash:
                continue
            if role not in ALLOWED_ROLES:
                logger.warning("Skipping user with unrecognized role in CSV")
                continue

            users[username] = CsvUserRecord(username=username, password_hash=password_hash, role=role)

    return users


def get_user(username: str) -> Optional[CsvUserRecord]:
    return load_users().get(username)


def save_users(users: Dict[str, CsvUserRecord]) -> None:
    path = users_csv_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        for record in users.values():
            writer.writerow(
                {"username": record.username, "password_hash": record.password_hash, "role": record.role}
            )
    try:
        path.chmod(0o600)
    except OSError:
        # chmod can fail on some platforms/filesystems (e.g. certain
        # network mounts) - not fatal, the file is still gitignored.
        pass
