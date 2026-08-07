"""
Organization-scoped, dry-run-capable cleanup of EXISTING placeholder
employer values (e.g. "Not stated", "N/A") already stored in
`Alumni.company` and, where safe, the matching organization-scoped
`Company` reference row - see app.services.company_placeholder_policy for
the reviewed, centralized placeholder list and normalization rules.

This module is intentionally isolated from
app.services.industry_backfill_service / app.services.classification_service
/ app.services.effective_profile_service / app.services.effective_alumni_service
/ app.routers.analytics_routes - it never imports, is never imported by,
and does not modify any of them. It only ever runs out-of-band, invoked by
scripts/cleanup_placeholder_company_values.py.

WRITES: this module writes ONLY:
  - Alumni.company, and only from a value that normalizes to an exact,
    reviewed placeholder match, to NULL.
  - Deletion of an organization-scoped Company row whose `name`
    normalizes to an exact, reviewed placeholder match (confirmed safe -
    see app.models.reference.Company: no foreign key references it from
    any other table, and no read endpoint currently serves it).
No other column, on any model, is ever written or deleted here.

PRIVACY: reports and manifests produced by this module contain only
Alumni/Company IDs and the (already-non-personal) placeholder text
itself - never alumni names, email addresses, LinkedIn URLs, or any
other personal field.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.alumni import Alumni, AlumniOrganization
from app.models.organization import Organization
from app.models.reference import Company
from app.services.company_placeholder_policy import is_placeholder_company_value


@dataclass
class CleanupReport:
    organization_slug: str
    organization_id: str
    mode: str  # "dry_run" | "applied"
    alumni_rows_examined: int
    alumni_rows_with_placeholder: int
    company_rows_examined: int
    company_rows_with_placeholder: int
    affected_alumni_counts_by_original_value: dict = field(default_factory=dict)
    affected_company_counts_by_original_value: dict = field(default_factory=dict)
    manifest: Optional[dict] = None


@dataclass
class RollbackReport:
    organization_slug: str
    organization_id: str
    alumni_in_manifest: int
    alumni_reverted: int
    companies_in_manifest: int
    companies_recreated: int


def run_cleanup(db: Session, organization: Organization, apply: bool = False) -> CleanupReport:
    """Computes (and, only when apply=True, stages via ORM attribute
    assignment/deletion - never commits) the placeholder cleanup for
    exactly one organization. Every query below is filtered to
    `organization.id` - never touches any other organization's rows.

    apply=False (dry run): makes NO changes at all - the report
    describes exactly what an apply run would do, computed read-only.
    apply=True: stages changes on already-loaded ORM objects; the caller
    decides when (or whether) to call db.commit() (see
    scripts/cleanup_placeholder_company_values.py).
    """
    alumni_rows = (
        db.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id, Alumni.is_active.is_(True))
        .all()
    )

    affected_alumni_counts: dict = {}
    alumni_changed_manifest: list = []
    for alumni in alumni_rows:
        if alumni.company is None or not is_placeholder_company_value(alumni.company):
            continue
        affected_alumni_counts[alumni.company] = affected_alumni_counts.get(alumni.company, 0) + 1
        alumni_changed_manifest.append({"id": alumni.id, "previous_company": alumni.company})
        if apply:
            alumni.company = None

    company_rows = db.query(Company).filter(Company.organization_id == organization.id).all()

    affected_company_counts: dict = {}
    companies_changed_manifest: list = []
    for company in company_rows:
        if not is_placeholder_company_value(company.name):
            continue
        affected_company_counts[company.name] = affected_company_counts.get(company.name, 0) + 1
        companies_changed_manifest.append(
            {"id": company.id, "previous_name": company.name, "previous_industry": company.industry}
        )
        if apply:
            db.delete(company)

    manifest = None
    if apply:
        manifest = {
            "organization_slug": organization.slug,
            "organization_id": organization.id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "alumni_changed": alumni_changed_manifest,
            "companies_changed": companies_changed_manifest,
        }

    return CleanupReport(
        organization_slug=organization.slug,
        organization_id=organization.id,
        mode="applied" if apply else "dry_run",
        alumni_rows_examined=len(alumni_rows),
        alumni_rows_with_placeholder=len(alumni_changed_manifest),
        company_rows_examined=len(company_rows),
        company_rows_with_placeholder=len(companies_changed_manifest),
        affected_alumni_counts_by_original_value=affected_alumni_counts,
        affected_company_counts_by_original_value=affected_company_counts,
        manifest=manifest,
    )


def rollback_cleanup(db: Session, organization: Organization, manifest: dict) -> RollbackReport:
    """Transactional (caller commits/rolls back), idempotent revert of
    exactly the rows recorded in `manifest` - restores the EXACT
    previously recorded `Alumni.company` value (including its original
    casing/whitespace), and recreates any deleted `Company` row with its
    exact previous id/name/industry. Refuses (raises ValueError, no
    changes staged) if the manifest's organization does not match
    `organization` by BOTH id and slug.
    """
    manifest_org_id = manifest.get("organization_id")
    manifest_org_slug = manifest.get("organization_slug")
    if manifest_org_id != organization.id or manifest_org_slug != organization.slug:
        raise ValueError(
            "Refusing to roll back: manifest organization "
            f"(slug={manifest_org_slug!r}, id={manifest_org_id!r}) does not match the "
            f"requested --organization (slug={organization.slug!r}, id={organization.id!r})"
        )

    alumni_in_manifest = manifest.get("alumni_changed", [])
    alumni_ids = [entry["id"] for entry in alumni_in_manifest]
    alumni_by_id = {}
    if alumni_ids:
        alumni_by_id = {
            alumni.id: alumni
            for alumni in db.query(Alumni)
            .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
            .filter(Alumni.id.in_(alumni_ids), AlumniOrganization.organization_id == organization.id)
            .all()
        }

    alumni_reverted = 0
    for entry in alumni_in_manifest:
        alumni = alumni_by_id.get(entry["id"])
        if alumni is None:
            continue
        alumni.company = entry["previous_company"]
        alumni_reverted += 1

    companies_in_manifest = manifest.get("companies_changed", [])
    company_ids = [entry["id"] for entry in companies_in_manifest]
    existing_company_ids: set = set()
    if company_ids:
        existing_company_ids = {
            company_id
            for (company_id,) in db.query(Company.id)
            .filter(Company.id.in_(company_ids), Company.organization_id == organization.id)
            .all()
        }

    companies_recreated = 0
    for entry in companies_in_manifest:
        if entry["id"] in existing_company_ids:
            continue  # already present (e.g. re-running rollback) - idempotent no-op
        db.add(
            Company(
                id=entry["id"],
                organization_id=organization.id,
                name=entry["previous_name"],
                industry=entry["previous_industry"],
            )
        )
        companies_recreated += 1

    return RollbackReport(
        organization_slug=organization.slug,
        organization_id=organization.id,
        alumni_in_manifest=len(alumni_in_manifest),
        alumni_reverted=alumni_reverted,
        companies_in_manifest=len(companies_in_manifest),
        companies_recreated=companies_recreated,
    )
