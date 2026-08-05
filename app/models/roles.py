"""Shared role/status enums used across models and schemas."""
from enum import Enum


class UserRole(str, Enum):
    """The application has exactly two primary roles. Admins manage shared
    content; alumni view published content and their own profile."""

    ADMIN = "admin"
    ALUMNI = "alumni"


SUPPORTED_USER_ROLES = frozenset({UserRole.ADMIN.value, UserRole.ALUMNI.value})


def resolve_effective_role(raw_role: str | None, fallback_role: str) -> str | None:
    """Resolves the role value to use for authorization/display: `raw_role`
    (e.g. a per-organization UserOrganization.role override) if set,
    otherwise `fallback_role` (e.g. the account's global users.role).

    Returns None - never a guessed/defaulted role - if the resolved value
    is not one of SUPPORTED_USER_ROLES. Callers MUST treat None as "deny/
    fail closed": an unrecognized role must never be silently treated as
    admin (or as anything else with elevated access).
    """
    candidate = raw_role if raw_role is not None else fallback_role
    return candidate if candidate in SUPPORTED_USER_ROLES else None


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    PENDING = "pending"


class LegalNameVerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    PENDING_REVIEW = "pending_review"
    VERIFIED = "verified"
    REJECTED = "rejected"
    CHANGE_REQUESTED = "change_requested"


class LegalNameChangeRequestStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProfileVisibility(str, Enum):
    PUBLIC = "public"
    ORGANIZATION = "organization"
    PRIVATE = "private"


class DataSource(str, Enum):
    IMPORTED = "imported"
    MANUALLY_ASSIGNED = "manually_assigned"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class LocationNormalizationStatus(str, Enum):
    NORMALIZED = "normalized"
    PARTIALLY_NORMALIZED = "partially_normalized"
    REMOTE = "remote"
    INTERNATIONAL = "international"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"
