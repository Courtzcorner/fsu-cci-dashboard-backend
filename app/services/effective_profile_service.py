"""
Write-time computation of a UserProfile's "effective" classification
cache (effective_full_name / effective_seniority / effective_career_category
/ effective_industry, each with provenance).

This runs ONCE per profile save (or link confirmation) - never per
analytics query - so the SQL effective-data layer
(app.services.effective_alumni_service) can be a plain CASE/COALESCE
join instead of re-running classification rules for every row on every
request. Nothing here ever writes to `Alumni` or touches the CSV import
pipeline.

Industry is NEVER guessed from a company name: the only two allowed
sources are a profile-supplied `current_industry` value, or an existing
admin-verified `Company.industry` mapping (the same mapping used by the
CSV import pipeline's classification_service) - otherwise unknown.
"""
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.organization import Organization
from app.models.reference import Company
from app.models.user_profile import UserProfile
from app.services.classification_service import derive_career_category, derive_seniority

INDUSTRY_SOURCE_PROFILE_SUPPLIED = "profile_supplied"
INDUSTRY_SOURCE_COMPANY_MAPPING = "company_mapping"
INDUSTRY_SOURCE_UNKNOWN = "unknown"


def _default_organization_id(db: Session) -> str | None:
    organization = (
        db.query(Organization).filter(Organization.slug == get_settings().default_organization_slug).first()
    )
    return organization.id if organization else None


def _lookup_company_industry(db: Session, employer: str | None) -> str | None:
    if not employer or not employer.strip():
        return None
    organization_id = _default_organization_id(db)
    if organization_id is None:
        return None
    row = (
        db.query(Company.industry)
        .filter(
            Company.organization_id == organization_id,
            Company.industry.isnot(None),
        )
        .filter(Company.name.ilike(employer.strip()))
        .first()
    )
    return row[0] if row else None


def recompute_profile_effective_fields(db: Session, profile: UserProfile) -> None:
    """Refreshes every `effective_*` cache column from the profile's
    current raw fields. Safe to call unconditionally on every save -
    idempotent, and cheap (O(1) queries, never scans the alumni table)."""
    first = (profile.first_name or "").strip()
    last = (profile.last_name or "").strip()
    full_name = f"{first} {last}".strip()
    profile.effective_full_name = full_name or None

    seniority_result = derive_seniority(profile.current_job_title)
    profile.effective_seniority = seniority_result.value
    profile.effective_seniority_source = seniority_result.source

    career_result = derive_career_category(profile.current_job_title)
    profile.effective_career_category = career_result.value
    profile.effective_career_category_source = career_result.source

    if profile.current_industry and profile.current_industry.strip():
        profile.effective_industry = profile.current_industry.strip()
        profile.effective_industry_source = INDUSTRY_SOURCE_PROFILE_SUPPLIED
        return

    mapped_industry = _lookup_company_industry(db, profile.current_employer)
    if mapped_industry:
        profile.effective_industry = mapped_industry
        profile.effective_industry_source = INDUSTRY_SOURCE_COMPANY_MAPPING
    else:
        profile.effective_industry = None
        profile.effective_industry_source = INDUSTRY_SOURCE_UNKNOWN
