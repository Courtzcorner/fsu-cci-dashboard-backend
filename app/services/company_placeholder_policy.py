"""
Centralized policy for placeholder/missing-data employer values (e.g.
"Not stated", "N/A", "Full-time") that must NEVER be treated as a real
company. This fixes the STARS National "Top Companies" bug where
"Not stated" / "not stated" / "Not Stated" each appeared as a separate,
seemingly-real company because company analytics grouped on raw,
un-normalized text.

Every company-derived analytics aggregation, the CSV import pipeline, and
the existing-data cleanup script (app.services.company_placeholder_cleanup_service /
scripts/cleanup_placeholder_company_values.py) MUST use
`is_placeholder_company_value()` or `company_placeholder_sql_exclusion()`
from this module - never duplicate this list or re-implement the
normalization elsewhere. This module holds only data + tiny, pure
normalization/predicate helpers - no database access, no ORM imports.

Matching rules (deliberately minimal and narrow):
  - case-insensitive
  - surrounding-whitespace-insensitive (stripped)
  - repeated internal whitespace collapsed (e.g. "not   stated" ==
    "not stated")
  - EXACT match only after normalization - never substring/contains/
    keyword/fuzzy/AI/web. "Not Stated Consulting", "Unknown Ventures",
    "Full-Time Technologies", "Student Services Inc.", "LinkedIn",
    "LinkedIn Corporation", "LinkedIn Learning", "LinkedIn Not Found
    Consulting", "LinkedIn Updated Solutions", "Not Found Consulting",
    "Not Found Technologies", "Foundry", and "Found Solutions" are real
    company names and must never be excluded - none of them equals a
    placeholder value even after normalization.
  - no punctuation stripping and no corporate-suffix stripping (unlike
    app.services.industry_mapping_data, which is a different, unrelated
    feature with different goals) - "full-time" and "full time" are
    therefore two distinct, separately-listed values, exactly as
    reviewed, and "Student Services Inc." never collapses toward
    "student".
"""
import re

# Reviewed, exact, normalized placeholder/missing-data values. Every entry
# here is a lowercase string with single spaces only (i.e. already in the
# form produced by normalize_for_placeholder_check() below).
PLACEHOLDER_COMPANY_VALUES: frozenset = frozenset({
    "not stated",
    "not specified",
    "n/a",
    "na",
    "none",
    "unknown",
    "unemployed",
    "not employed",
    "full-time",
    "full time",
    "part-time",
    "part time",
    "student",
    "linkedin not found",
    "linkedin not updated",
    "not found",
})

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_placeholder_check(value: str | None) -> str | None:
    """Strip surrounding whitespace -> collapse repeated internal
    whitespace -> case-fold. Returns None for blank/whitespace-only or
    None input. This normalization is used ONLY for the placeholder
    check above - it is intentionally not shared with, and does not
    replace, app.services.industry_backfill_service.normalize_company_name
    (which additionally strips punctuation/corporate suffixes and
    resolves aliases for a completely different purpose)."""
    if value is None:
        return None
    text = _WHITESPACE_RE.sub(" ", value.strip()).lower()
    return text or None


def is_placeholder_company_value(value: str | None) -> bool:
    """True only for an EXACT normalized match against
    PLACEHOLDER_COMPANY_VALUES - never a substring/contains check."""
    normalized = normalize_for_placeholder_check(value)
    return normalized is not None and normalized in PLACEHOLDER_COMPANY_VALUES


def company_placeholder_sql_exclusion(column):
    """A SQLAlchemy filter condition excluding any row whose `column`
    (whitespace/case-normalized the same way as is_placeholder_company_value)
    exactly matches a placeholder value. Apply this to any query that
    groups, counts, or lists a company/employer column for analytics -
    never apply it to an unrelated column (industry, seniority,
    university, etc.).

    Note: SQL `TRIM()` only strips leading/trailing whitespace (matching
    normalize_for_placeholder_check's strip() step) - it does not collapse
    repeated INTERNAL whitespace. Values with irregular internal spacing
    (e.g. "not   stated") are extremely rare CSV artifacts and are still
    correctly handled by the Python-level is_placeholder_company_value()
    used during CSV import and cleanup, where the value is normalized
    once and stored/compared, so no un-normalized internal-whitespace
    variant should ever persist in the database going forward.
    """
    from sqlalchemy import func

    return func.lower(func.trim(column)).notin_(PLACEHOLDER_COMPANY_VALUES)
