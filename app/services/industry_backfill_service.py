"""
Deterministic, curated-mapping-only backfill for `Company.industry` and
blank `Alumni.industry` rows, using ONLY the reviewed mappings in
app.services.industry_mapping_data. See that module's docstring for the
full list of reviewed mappings/aliases and the guardrails behind them.

This module is intentionally isolated - it does not import, is not
imported by, and does not modify app.services.classification_service,
app.services.effective_profile_service, app.services.effective_alumni_service,
or app.routers.analytics_routes. It never runs during a GET request - it
is only ever invoked by scripts/backfill_company_industry.py, run
manually/out-of-band by an operator.

WRITES: This module writes ONLY:
  - Company.industry, and only when currently NULL
  - Alumni.industry, and only when currently NULL
  - Alumni.industry_source = "company_mapping", only alongside an
    Alumni.industry write made by this module
No other column, on any model, is ever written here. `Alumni.company`
and `Company.name` (the original imported text) are only ever READ, never
written or renamed.

CLASSIFICATION GUARDRAILS (see app.services.industry_mapping_data for the
full rationale):
  - Deterministic, curated mappings only - no AI/model calls, no live web
    requests, no probabilistic or keyword/substring inference.
  - Never classifies during analytics/GET request execution, and never
    mutates outside an explicit, operator-run backfill.
  - An unknown or ambiguous company is left unclassified - never guessed.
  - Alias matching is normalized-EXACT-match only - "Capital" never
    matches "Capital One", "Florida State" never matches "Florida State
    University" unless explicitly aliased.
  - A blocked employer/status value (e.g. "Full-time", "Student") is
    never classified as a company.
  - Every write here only ever fills a currently-NULL field (idempotent:
    re-running finds nothing left to do once applied).

PRE-EXISTING, NOT FIXED HERE (flagged only, per Phase 1 approval - do not
silently fix, out of the approved file list for this phase):
app.services.effective_profile_service._lookup_company_industry() scopes
its Company lookup to get_settings().default_organization_slug rather
than the alumni's actual organization, so a confirmed profile's
company-mapping industry override is currently org-mis-scoped for any
non-default organization. This module does not touch UserProfile at all
(it is not in the approved industry-only write list for this phase), so
it neither causes nor worsens that pre-existing issue - it is noted here
only so it is not silently ignored.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.alumni import Alumni, AlumniOrganization
from app.models.organization import Organization
from app.models.reference import Company
from app.services.industry_mapping_data import (
    ALLOWED_SHORT_ALIASES,
    APPROVED_COMPANY_ALIASES,
    BLOCKED_EMPLOYER_VALUES,
    GLOBAL_DEFAULT_COMPANY_INDUSTRY,
    ORGANIZATION_COMPANY_INDUSTRY_OVERRIDES,
    SHORT_ALIAS_MAX_LENGTH,
)

INDUSTRY_SOURCE_COMPANY_MAPPING = "company_mapping"

_WHITESPACE_RE = re.compile(r"\s+")
# A fixed, narrow punctuation set only - periods/commas/quotes commonly
# used in "Inc.", "Co.", "St. Jude's", etc. Never a broad "strip anything
# non-alphanumeric" pass, which could collapse otherwise-distinct names.
_PUNCTUATION_RE = re.compile(r"[.,'\"]")
# Stripped ONLY as a single trailing whole word, never from the middle
# and never partially overlapping the preceding word (word-boundary
# anchored at the end of the already whitespace/punctuation-normalized
# string).
_CORPORATE_SUFFIXES = ("inc", "llc", "corp", "corporation", "co", "ltd", "company", "plc", "lp", "llp")
_CORP_SUFFIX_RE = re.compile(r"\s+(" + "|".join(_CORPORATE_SUFFIXES) + r")$")

_BLOCKED_EMPLOYER_VALUES_NORMALIZED: frozenset = frozenset(
    _WHITESPACE_RE.sub(" ", _PUNCTUATION_RE.sub("", value.strip().lower())).strip()
    for value in BLOCKED_EMPLOYER_VALUES
)


def normalize_company_name(value: Optional[str]) -> Optional[str]:
    """Case-fold -> strip surrounding whitespace -> strip a fixed
    punctuation set -> collapse repeated internal whitespace -> strip a
    single trailing corporate suffix word -> exact-match alias
    substitution. Never a substring/contains match at any step - a
    normalized value either equals an alias key exactly, or it doesn't.
    Returns None for blank/whitespace-only input.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    text = text.lower()
    text = _PUNCTUATION_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if not text:
        return None
    stripped = _CORP_SUFFIX_RE.sub("", text).strip()
    normalized = stripped or text
    return APPROVED_COMPANY_ALIASES.get(normalized, normalized)


def is_blocked_employer_value(normalized_name: str) -> bool:
    return normalized_name in _BLOCKED_EMPLOYER_VALUES_NORMALIZED


def resolve_curated_industry(normalized_name: str, organization_slug: str) -> Optional[str]:
    """Exact dict lookup only - organization-specific override first,
    then the global reviewed default. Returns None (never guesses) if
    neither has an entry for this exact normalized name."""
    org_overrides = ORGANIZATION_COMPANY_INDUSTRY_OVERRIDES.get(organization_slug, {})
    if normalized_name in org_overrides:
        return org_overrides[normalized_name]
    return GLOBAL_DEFAULT_COMPANY_INDUSTRY.get(normalized_name)


def validate_industry_mapping_data() -> None:
    """Fails fast (raises ValueError) if app.services.industry_mapping_data
    ever becomes internally inconsistent. Run once at import time below,
    and safe to call again directly from tests."""
    for name, industry in GLOBAL_DEFAULT_COMPANY_INDUSTRY.items():
        if not industry or not industry.strip():
            raise ValueError(f"Blank industry for curated mapping '{name}'")

    for org_slug, overrides in ORGANIZATION_COMPANY_INDUSTRY_OVERRIDES.items():
        for name, industry in overrides.items():
            if not industry or not industry.strip():
                raise ValueError(f"Blank industry for org override '{org_slug}'/'{name}'")

    all_canonical_keys = set(GLOBAL_DEFAULT_COMPANY_INDUSTRY) | {
        key for overrides in ORGANIZATION_COMPANY_INDUSTRY_OVERRIDES.values() for key in overrides
    }
    for alias, canonical in APPROVED_COMPANY_ALIASES.items():
        if not alias:
            raise ValueError("Blank alias key")
        if len(alias) <= SHORT_ALIAS_MAX_LENGTH and alias not in ALLOWED_SHORT_ALIASES:
            raise ValueError(
                f"Alias '{alias}' is <= {SHORT_ALIAS_MAX_LENGTH} chars and not in ALLOWED_SHORT_ALIASES"
            )
        if canonical not in all_canonical_keys:
            raise ValueError(f"Alias '{alias}' points to unknown canonical company '{canonical}'")

    overlap = _BLOCKED_EMPLOYER_VALUES_NORMALIZED & (all_canonical_keys | set(APPROVED_COMPANY_ALIASES))
    if overlap:
        raise ValueError(f"Blocked employer values overlap curated mappings/aliases: {overlap}")


validate_industry_mapping_data()


@dataclass
class BackfillReport:
    organization_slug: str
    organization_id: str
    mode: str  # "dry_run" | "applied"
    company_rows_examined: int
    company_rows_already_classified: int
    company_mappings_proposed: int
    alumni_rows_examined: int
    alumni_records_already_classified: int
    alumni_records_classified: int
    invalid_employer_values_skipped: int
    unknown_companies_skipped: int
    alumni_without_company_skipped: int
    proposed_industry_counts: dict = field(default_factory=dict)
    unknown_employer_names: list = field(default_factory=list)
    unknown_employer_names_truncated: bool = False
    manifest: Optional[dict] = None


@dataclass
class RollbackReport:
    organization_slug: str
    organization_id: str
    companies_in_manifest: int
    alumni_in_manifest: int
    companies_reverted: int
    alumni_reverted: int


def run_backfill(
    db: Session,
    organization: Organization,
    apply: bool = False,
    unknown_employer_limit: int = 20,
    show_all_unknown: bool = False,
) -> BackfillReport:
    """Computes (and, only when apply=True, stages via ORM attribute
    assignment - never commits) the industry backfill for exactly one
    organization. Never touches any other organization's Company/Alumni
    rows - every query below is filtered to `organization.id`.

    apply=False (dry run): makes NO attribute changes at all - the
    report describes exactly what an apply run would do, computed
    read-only. The caller must not commit after a dry run (see
    scripts/backfill_company_industry.py).

    apply=True: stages attribute changes on already-loaded ORM objects.
    Still does not commit - the caller decides when (or whether) to
    call db.commit(), after printing/confirming the returned report and
    manifest. On any exception raised by this function, no field
    written by it should be treated as durable - the caller must call
    db.rollback() (see the CLI script), which discards every staged
    change made here because nothing was ever flushed/committed.
    """
    org_slug = organization.slug

    company_rows = db.query(Company).filter(Company.organization_id == organization.id).all()

    company_rows_already_classified = 0
    company_mappings_proposed = 0
    companies_changed_manifest: list = []
    effective_company_industry: dict = {}

    for company in company_rows:
        if company.industry is not None:
            company_rows_already_classified += 1
            normalized_existing = normalize_company_name(company.name)
            if normalized_existing is not None:
                effective_company_industry.setdefault(normalized_existing, company.industry)
            continue

        normalized = normalize_company_name(company.name)
        if normalized is None or is_blocked_employer_value(normalized):
            continue

        curated = resolve_curated_industry(normalized, org_slug)
        if curated is None:
            continue

        company_mappings_proposed += 1
        effective_company_industry[normalized] = curated
        companies_changed_manifest.append({"id": company.id, "previous_industry": company.industry})
        if apply:
            company.industry = curated

    alumni_rows = (
        db.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id, Alumni.is_active.is_(True))
        .all()
    )

    alumni_records_already_classified = 0
    alumni_records_classified = 0
    invalid_employer_values_skipped = 0
    unknown_companies_skipped = 0
    alumni_without_company_skipped = 0
    proposed_industry_counts: dict = {}
    unknown_employer_counter: dict = {}
    alumni_changed_manifest: list = []

    for alumni in alumni_rows:
        if alumni.industry is not None:
            alumni_records_already_classified += 1
            continue

        normalized = normalize_company_name(alumni.company)
        if normalized is None:
            alumni_without_company_skipped += 1
            continue

        if is_blocked_employer_value(normalized):
            invalid_employer_values_skipped += 1
            continue

        resolved = effective_company_industry.get(normalized)
        if resolved is None:
            resolved = resolve_curated_industry(normalized, org_slug)

        if resolved is None:
            unknown_companies_skipped += 1
            unknown_employer_counter[normalized] = unknown_employer_counter.get(normalized, 0) + 1
            continue

        alumni_records_classified += 1
        proposed_industry_counts[resolved] = proposed_industry_counts.get(resolved, 0) + 1
        alumni_changed_manifest.append(
            {
                "id": alumni.id,
                "previous_industry": alumni.industry,
                "previous_industry_source": alumni.industry_source,
            }
        )
        if apply:
            alumni.industry = resolved
            alumni.industry_source = INDUSTRY_SOURCE_COMPANY_MAPPING

    sorted_unknown = sorted(unknown_employer_counter.items(), key=lambda item: (-item[1], item[0]))
    truncated = (not show_all_unknown) and len(sorted_unknown) > unknown_employer_limit
    limited_unknown = sorted_unknown if show_all_unknown else sorted_unknown[:unknown_employer_limit]

    manifest = None
    if apply:
        manifest = {
            "organization_slug": org_slug,
            "organization_id": organization.id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "companies_changed": companies_changed_manifest,
            "alumni_changed": alumni_changed_manifest,
        }

    return BackfillReport(
        organization_slug=org_slug,
        organization_id=organization.id,
        mode="applied" if apply else "dry_run",
        company_rows_examined=len(company_rows),
        company_rows_already_classified=company_rows_already_classified,
        company_mappings_proposed=company_mappings_proposed,
        alumni_rows_examined=len(alumni_rows),
        alumni_records_already_classified=alumni_records_already_classified,
        alumni_records_classified=alumni_records_classified,
        invalid_employer_values_skipped=invalid_employer_values_skipped,
        unknown_companies_skipped=unknown_companies_skipped,
        alumni_without_company_skipped=alumni_without_company_skipped,
        proposed_industry_counts=proposed_industry_counts,
        unknown_employer_names=[{"name": name, "count": count} for name, count in limited_unknown],
        unknown_employer_names_truncated=truncated,
        manifest=manifest,
    )


def rollback_backfill(db: Session, organization: Organization, manifest: dict) -> RollbackReport:
    """Transactional (caller commits/rolls back - see the CLI script),
    idempotent revert of exactly the rows recorded in `manifest` (the
    shape produced by run_backfill's `manifest` field) - restores the
    EXACT previously recorded value for each field, never assuming
    NULL/"unknown". Refuses to run (raises ValueError, no changes staged)
    if the manifest's organization does not match `organization` by BOTH
    id and slug. A row that no longer exists (e.g. deleted since the
    original run) is skipped rather than erroring, so a partial/already-
    reverted manifest can always be re-applied safely (idempotent).
    """
    manifest_org_id = manifest.get("organization_id")
    manifest_org_slug = manifest.get("organization_slug")
    if manifest_org_id != organization.id or manifest_org_slug != organization.slug:
        raise ValueError(
            "Refusing to roll back: manifest organization "
            f"(slug={manifest_org_slug!r}, id={manifest_org_id!r}) does not match the "
            f"requested --organization (slug={organization.slug!r}, id={organization.id!r})"
        )

    companies_in_manifest = manifest.get("companies_changed", [])
    alumni_in_manifest = manifest.get("alumni_changed", [])

    company_ids = [entry["id"] for entry in companies_in_manifest]
    companies_by_id = {}
    if company_ids:
        companies_by_id = {
            company.id: company
            for company in db.query(Company)
            .filter(Company.id.in_(company_ids), Company.organization_id == organization.id)
            .all()
        }

    companies_reverted = 0
    for entry in companies_in_manifest:
        company = companies_by_id.get(entry["id"])
        if company is None:
            continue
        company.industry = entry["previous_industry"]
        companies_reverted += 1

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
        alumni.industry = entry["previous_industry"]
        alumni.industry_source = entry["previous_industry_source"]
        alumni_reverted += 1

    return RollbackReport(
        organization_slug=organization.slug,
        organization_id=organization.id,
        companies_in_manifest=len(companies_in_manifest),
        alumni_in_manifest=len(alumni_in_manifest),
        companies_reverted=companies_reverted,
        alumni_reverted=alumni_reverted,
    )
