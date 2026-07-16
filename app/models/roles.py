"""Shared role/status enums used across models and schemas."""
from enum import Enum


class UserRole(str, Enum):
    """The application has exactly two primary roles. Admins manage shared
    content; alumni view published content and their own profile."""

    ADMIN = "admin"
    ALUMNI = "alumni"


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
