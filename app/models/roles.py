"""Shared role/status enums used across models and schemas."""
from enum import Enum


class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    ORGANIZATION_ADMIN = "organization_admin"
    ALUMNI = "alumni"
    VIEWER = "viewer"


# Priority order used to pick a "primary" role to show at the top level of
# the login response when a user belongs to multiple organizations.
ROLE_PRIORITY = [
    UserRole.SUPER_ADMIN,
    UserRole.ORGANIZATION_ADMIN,
    UserRole.ALUMNI,
    UserRole.VIEWER,
]


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    PENDING = "pending"


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
