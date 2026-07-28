"""
Validation rules for POST /auth/complete-first-login. Kept in one place
so the username/password policy is unambiguous and independently
testable, and so it can never silently diverge between the request
schema and the route handler.
"""
import re

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.user import User

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")
TEMPORARY_PASSWORD = "testtest"
MIN_PASSWORD_LENGTH = 10


class ValidationError(Exception):
    """Raised with a user-facing, non-sensitive message (never includes
    the submitted password)."""


def validate_new_username(raw_username: str, current_username: str, db: Session) -> str:
    if raw_username != raw_username.strip():
        raise ValidationError("Username may not have leading or trailing spaces")
    if not raw_username:
        raise ValidationError("Username is required")
    if not USERNAME_PATTERN.match(raw_username):
        raise ValidationError(
            "Username must be 3-40 characters and may only contain letters, numbers, underscores, "
            "periods, and hyphens"
        )
    if raw_username.lower() == current_username.lower():
        raise ValidationError("Please choose a new username different from your temporary username")

    existing = db.query(User).filter(func.lower(User.username) == raw_username.lower()).first()
    if existing is not None:
        raise ValidationError("That username is already taken")

    return raw_username


def validate_new_password(new_password: str, confirm_password: str) -> str:
    if new_password != confirm_password:
        raise ValidationError("Password and confirmation do not match")
    if new_password.strip().lower() == TEMPORARY_PASSWORD:
        raise ValidationError("You cannot reuse the temporary password")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if not re.search(r"[A-Z]", new_password):
        raise ValidationError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", new_password):
        raise ValidationError("Password must contain at least one lowercase letter")
    if not re.search(r"[0-9]", new_password):
        raise ValidationError("Password must contain at least one number")

    return new_password
