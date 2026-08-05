"""
CSV import pipeline for alumni records, scoped to a single organization.

Responsibilities:
- normalize arbitrary spreadsheet column headers (via `normalize_header`)
  and map them to canonical Alumni fields (via `FIELD_ALIASES`)
- normalize whitespace / blank -> null, preserving 0 and False
- preserve `location_original` and normalize location via
  `location_normalization_service` (only once `location_original` is
  actually populated)
- normalize LinkedIn URLs
- parse graduation years
- validate required fields
- detect + update duplicates (never merge purely on name)
- on update, never overwrite an existing nonnull database value with a
  blank CSV value ("safe update")
- return created/updated/skipped/failed counts, row-specific errors, and
  temporary import diagnostics (recognized/unrecognized headers, per-field
  fill counts) to make CSV mapping issues visible without a debugger
"""
import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.alumni import Alumni, AlumniOrganization
from app.models.audit import CSVImport
from app.models.organization import Organization
from app.models.reference import Company, Industry, University
from app.services.audit_service import record_audit_log
from app.services.classification_service import build_company_industry_map, classify_alumni_fields
from app.services.content_version_service import bump_for_csv_import
from app.services.location_normalization_service import normalize_city_state, normalize_location

logger = logging.getLogger(__name__)

# Temporary deploy marker: the import response and startup log both emit
# this value so we can confirm Render is running replace-mode code rather
# than a stale merge/upsert build. Safe to remove once production has
# been verified end-to-end.
IMPORT_LOGIC_VERSION = "replace-v2"
logger.info("CSV replacement import logic %s loaded", IMPORT_LOGIC_VERSION)

REQUIRED_FIELDS = {"first_name", "last_name"}

# Values that mean "no data" once trimmed + lowercased. Zero and False are
# intentionally NOT in this set - they are meaningful values, not blanks.
_NULL_TOKENS = {"", "null", "none", "nan", "n/a", "na", "-"}

# Normalized-header substrings used for the temporary "check the real
# column values" logging below - any recognized OR unrecognized column
# whose normalized name contains one of these is worth inspecting when a
# field is unexpectedly coming back null.
_DEBUG_KEYWORD_SUBSTRINGS = (
    "company", "employer", "location", "city", "state", "school", "university",
    "college", "degree", "major", "graduation", "grad", "year",
)

FSU_CCI_SLUG = "fsu-cci"
FSU_CCI_DEFAULT_UNIVERSITY = "Florida State University"


def normalize_header(header: str) -> str:
    """Turn an arbitrary spreadsheet header into a canonical snake_case
    token, e.g. "Current Job Title" -> "current_job_title".

    Handles: leading/trailing whitespace, UTF-8 BOM, non-breaking/hidden
    whitespace, casing, spaces/hyphens -> underscore, punctuation removal,
    and duplicate/leading/trailing underscore collapsing.
    """
    if header is None:
        return ""
    text = header.replace("\ufeff", "")  # UTF-8 BOM, in case it wasn't already stripped
    text = text.replace("\xa0", " ")  # non-breaking space
    text = text.strip()
    text = text.lower()
    text = text.replace("-", "_")
    text = re.sub(r"\s+", "_", text)  # any whitespace run (space/tab/newline) -> underscore
    text = re.sub(r"[^a-z0-9_]", "", text)  # drop punctuation (periods, slashes, parens, apostrophes, ...)
    text = re.sub(r"_+", "_", text)  # collapse duplicate underscores
    return text.strip("_")


# Explicit alias map: canonical Alumni-import field -> ordered list of
# normalize_header() outputs, in PRIORITY order. When a CSV happens to
# contain more than one column that maps to the same field (e.g. both
# "Current Job Title" and "LinkedIn Job Title"), the first nonblank value
# found in this priority order wins.
FIELD_ALIASES: dict[str, list[str]] = {
    "graduation_year": [
        "graduation_year", "grad_year", "year_graduated", "class_year", "graduation_date", "graduation",
    ],
    "major": [
        "major", "major_name", "program", "program_name", "program_of_study", "field_of_study",
        "area_of_study",
    ],
    # NOTE: a generic "Education" column is intentionally NOT aliased here -
    # its value is ambiguous (it might be an institution name OR a degree)
    # and is resolved dynamically by `_classify_education_value` below.
    "degree": [
        "degree", "degree_name", "degree_type", "credential", "education_degree",
    ],
    "university": [
        "university", "school_name", "school", "college", "institution", "institution_name",
        "education_institution", "education_school",
    ],
    # Explicit priority per spec: LinkedIn-sourced value first, then
    # "current", then the plain/generic column, then "existing" last.
    "job_title": [
        "linkedin_job_title", "current_job_title", "job_title", "title", "current_title",
        "position", "current_position", "existing_job_title",
    ],
    # NOTE: the generic "organization" column is intentionally excluded -
    # on multi-org deployments that column identifies the *portal*
    # organization (e.g. "fsu-cci"), not the alumni's employer. Only the
    # more specific "organization_name" alias is treated as an employer.
    "company": [
        "linkedin_company", "current_employer", "current_company", "company", "company_name",
        "employer", "existing_company", "organization_name", "workplace",
    ],
    "industry": [
        "industry", "current_industry", "career_industry",
    ],
    "career_category": [
        "career_category", "career_field", "job_category", "career_path", "functional_area",
    ],
    "seniority": [
        "seniority", "seniority_level", "career_level", "job_level", "level",
    ],
    "location_original": [
        "linkedin_location", "current_location", "job_location", "current_job_location", "location",
        "existing_location", "city_state", "work_location", "geographic_location", "raw_location", "address",
    ],
    "city": ["city", "current_city", "job_city", "location_city", "linkedin_city"],
    "state": [
        "state", "current_state", "job_state", "location_state", "linkedin_state",
        "state_code", "state_abbreviation",
    ],
    "state_code": ["state_code", "state_abbreviation", "state_abbrev"],
    "country": ["country", "current_country", "job_country"],
    "metro_area": ["metro_area", "metropolitan_area", "metro", "region"],
    "display_location": ["display_location", "formatted_location", "location_display"],
    "linkedin_url": ["linkedin_url", "linkedin", "linkedin_profile", "profile_url"],
    "first_name": ["first_name", "firstname", "first", "student_firstname", "student_first_name", "fname"],
    "last_name": ["last_name", "lastname", "last", "student_lastname", "student_last_name", "lname"],
    "full_name": ["full_name", "name", "student_name", "alumni_name"],
    "verification_status": ["verification_status", "verified_status", "education_match_status"],
    "verification_date": ["verification_date", "verified_date", "verification_timestamp", "date_verified"],
    "verified": ["verified", "is_verified"],
    # Recognized (won't be flagged as "unrecognized") but not yet persisted
    # to an Alumni column - no schema field exists for these today.
    "profile_headline": ["profile_headline", "headline", "linkedin_headline"],
    "employment_tenure": ["employment_tenure", "tenure"],
    "employment_type": ["employment_type", "job_type"],
    # Unlike the three fields above, "email" IS persisted - to
    # alumni.email - and used (after normalized LinkedIn URL) as a
    # dedupe/match key on reimport. See field_values["email"] below.
    "email": ["email"],
    "notes": ["notes", "note", "comments", "comment"],
}

# Note: "profile_headline", "employment_tenure", and "employment_type" are
# recognized (won't show up as unrecognized_headers) but have no backing
# Alumni column yet, so they are intentionally never added to
# field_values below. "email" and "notes" ARE persisted - see above.

# Headers that are recognized (so they never show up in
# unrecognized_headers) but whose target field can't be decided purely
# from the header name - the row's actual value decides it. See
# `_classify_education_value`.
EXTRA_RECOGNIZED_ALIASES: dict[str, str] = {
    "education": "education_ambiguous",
}

# Reverse lookup: normalized header alias -> canonical field name. Used to
# classify each incoming CSV header as recognized/unrecognized.
ALIAS_TO_FIELD: dict[str, str] = {}
for _field_name, _aliases in FIELD_ALIASES.items():
    for _alias in _aliases:
        ALIAS_TO_FIELD.setdefault(_alias, _field_name)
for _alias, _field_name in EXTRA_RECOGNIZED_ALIASES.items():
    ALIAS_TO_FIELD.setdefault(_alias, _field_name)

_EDUCATION_UNIVERSITY_KEYWORDS = ("university", "college", "institute", "school", "academy", "fsu")
_EDUCATION_DEGREE_PATTERN = re.compile(
    r"\b(bachelor'?s?|master'?s?|mba|ph\.?d\.?|doctorate|associate'?s?|certificate|"
    r"b\.?a\.?|b\.?s\.?|m\.?a\.?|m\.?s\.?)\b",
    re.IGNORECASE,
)


def _classify_education_value(value: str) -> str:
    """Classifies an ambiguous "Education" column value as either an
    institution name ("university") or a credential ("degree").

    Institution-style content always wins (e.g. "Bachelor of Science,
    Florida State University" is a university value, not a degree one),
    since the whole point of this field is to identify the alumni's
    school. An unrecognized free-text value defaults to "degree" to match
    this column's traditional meaning.
    """
    lowered = value.lower()
    if any(keyword in lowered for keyword in _EDUCATION_UNIVERSITY_KEYWORDS):
        return "university"
    if _EDUCATION_DEGREE_PATTERN.search(value):
        return "degree"
    return "degree"


def _clean_value(value: str | None) -> str | None:
    """Trim whitespace and convert blank/NaN/null-ish tokens to None.
    Never touches a genuinely meaningful value (e.g. "0", "false", "No").
    """
    if value is None:
        return None
    stripped = value.strip()
    if stripped.lower() in _NULL_TOKENS:
        return None
    return stripped


def _first_nonblank_with_source(
    row_by_alias: dict[str, str | None], aliases: list[str]
) -> tuple[str | None, str | None]:
    """Returns (value, normalized_alias_that_provided_it). Both None if no
    alias in the priority list had a nonblank value in this row.
    """
    for alias in aliases:
        value = row_by_alias.get(alias)
        if value is not None:
            return value, alias
    return None, None


def _normalize_linkedin_url(value: str | None) -> str | None:
    """Normalizes a LinkedIn URL into a stable, comparable form so the same
    profile always dedupe-matches across uploads regardless of superficial
    formatting differences (scheme, www., country subdomain, trailing slash,
    query string, fragment, casing).
    """
    if not value:
        return None
    url = value.strip()
    if not url:
        return None
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"
    # Collapse http -> https, drop query strings and #fragments, drop a
    # trailing slash, and lowercase. Prefer the stable /in/<slug> (or
    # /pub/<slug>) identity when present so www vs non-www and
    # uk.linkedin.com vs linkedin.com all collapse to the same key.
    url = url.replace("http://", "https://")
    url = url.split("?")[0]
    url = url.split("#")[0]
    url = url.rstrip("/")
    profile_match = re.search(
        r"linkedin\.com/(in|pub)/([^/?#]+)", url, flags=re.IGNORECASE
    )
    if profile_match:
        kind = profile_match.group(1).lower()
        slug = profile_match.group(2).strip("/").lower()
        if slug:
            # Canonical stored + match key: always https, never www.
            return f"https://linkedin.com/{kind}/{slug}"
    url = re.sub(r"^https://([a-z0-9-]+\.)?www\.", "https://", url, flags=re.IGNORECASE)
    url = re.sub(r"^https://www\.", "https://", url, flags=re.IGNORECASE)
    return url.lower()


def _parse_graduation_year(value: str | None) -> tuple[int | None, str | None]:
    if not value:
        return None, None
    match = re.search(r"(19|20)\d{2}", value)
    if not match:
        return None, f"Could not parse graduation year from '{value}'"
    return int(match.group(0)), None


def _parse_bool(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().lower() in {"true", "yes", "1", "y", "verified"}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    # ISO first (e.g. "2026-06-15" or "2026-06-15T00:00:00Z").
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_whitespace(value: str | None) -> str | None:
    """Collapses internal whitespace runs to a single space (e.g. tabs,
    double spaces from copy-pasted spreadsheet data). Leading/trailing
    whitespace is already stripped by `_clean_value`.
    """
    if not value:
        return value
    return re.sub(r"\s+", " ", value).strip()


def _normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().lower()


def _normalize_name_part(value: str | None) -> str:
    """Normalize a first/last name for matching: lowercase, strip
    parenthetical nicknames (e.g. "John (Johnny)"), drop punctuation,
    and collapse whitespace. "O'Neil" and "ONeil" intentionally remain
    distinct after punctuation removal only if letters differ; both
    "O'Neil" and "O’Neil" become "o neil".
    """
    if not value:
        return ""
    text = value.strip().lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _compute_dedupe_key(
    linkedin_url: str | None, email: str | None, first_name: str, last_name: str
) -> str:
    """Priority: normalized LinkedIn URL > normalized email > normalized
    first+last name. Used to collapse duplicate rows WITHIN a single CSV
    upload so the same person is never created/updated twice from one file.
    """
    if linkedin_url:
        return f"linkedin:{linkedin_url}"
    normalized_email = _normalize_email(email)
    if normalized_email:
        return f"email:{normalized_email}"
    return f"name:{_normalize_name_part(first_name)}:{_normalize_name_part(last_name)}"


def _describe_match_attempt(
    linkedin_url: str | None,
    email: str | None,
    first_name: str,
    last_name: str,
) -> tuple[list[str], str]:
    """Return (strategies_attempted, unmatched_reason) for create diagnostics."""
    strategies: list[str] = []
    if linkedin_url:
        strategies.append("linkedin_url")
    if email:
        strategies.append("email")
    strategies.append("first_name+last_name")

    if linkedin_url and email:
        reason = (
            "no existing alumni matched normalized linkedin_url or email; "
            "name fallback also found no match"
        )
    elif linkedin_url:
        reason = (
            "no existing alumni matched normalized linkedin_url; "
            "no email provided; name fallback also found no match"
        )
    elif email:
        reason = (
            "no linkedin_url provided; normalized email did not match any "
            "existing alumni; name fallback also found no match"
        )
    else:
        reason = (
            "no linkedin_url or email provided; normalized first+last name "
            "found no existing alumni"
        )
    return strategies, reason


@dataclass
class RowOutcome:
    action: str  # "created" | "updated" | "skipped" | "failed"
    error: str | None = None


@dataclass
class ImportSummary:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    archived: int = 0  # alias: "deactivated" in the API response
    skipped: int = 0
    failed: int = 0
    row_errors: list[dict] = field(default_factory=list)
    csv_import_id: str | None = None
    filename: str | None = None
    database_total: int = 0  # legacy alias for active_database_total, kept for backward compatibility
    active_database_total: int = 0
    # Total alumni_organizations rows for this organization regardless of
    # is_active - i.e. every row ever created, including everything this
    # import just archived. NEVER use this as the dashboard total; it is
    # provided purely for admin/debugging visibility into how much history
    # exists versus how much is currently active.
    historical_database_total: int = 0
    # Temporary deploy marker confirming this response came from the
    # replace-mode import path (not a stale merge/upsert build).
    import_logic_version: str = IMPORT_LOGIC_VERSION
    # --- Line/row accounting ---
    # physical_lines counts every line in the raw uploaded file (including
    # the header); header_rows is 0 or 1; data_rows = physical_lines minus
    # header_rows, i.e. "how many alumni rows the file actually contains,
    # independent of how many parsed cleanly."
    csv_physical_lines: int = 0
    csv_header_rows: int = 0
    csv_data_rows: int = 0
    csv_rows_received: int = 0  # legacy alias for csv_data_rows
    csv_valid_rows: int = 0
    csv_invalid_rows: int = 0
    csv_rows_valid: int = 0  # legacy alias for csv_valid_rows
    csv_rows_invalid: int = 0  # legacy alias for csv_invalid_rows
    csv_duplicate_rows: int = 0
    status: str = "complete"
    # --- Temporary diagnostics (see admin_routes/ImportResult) ---
    recognized_headers: list[str] = field(default_factory=list)
    unrecognized_headers: list[str] = field(default_factory=list)
    rows_with_graduation_year: int = 0
    rows_with_major: int = 0
    rows_with_university: int = 0
    rows_with_job_title: int = 0
    rows_with_company: int = 0
    rows_with_location: int = 0
    rows_with_city: int = 0
    rows_with_state: int = 0
    rows_with_raw_city: int = 0
    rows_with_raw_state: int = 0
    rows_with_constructed_location: int = 0
    # --- Additional temporary debugging fields (first data row only) ---
    first_row_original: dict = field(default_factory=dict)
    first_row_normalized: dict = field(default_factory=dict)
    selected_company_column: str | None = None
    selected_location_column: str | None = None
    selected_city_column: str | None = None
    selected_state_column: str | None = None
    selected_university_column: str | None = None
    selected_degree_column: str | None = None
    selected_major_column: str | None = None
    selected_graduation_year_column: str | None = None
    # --- Duplicate-matching audit diagnostics (temporary) ---
    # For each newly created alumni row: normalized identifiers + which
    # matching strategies were attempted and why none matched. Used to
    # audit the recurring "6 created on every upload" production bug.
    newly_created_identifiers: list[dict] = field(default_factory=list)
    # Count of newly-created rows whose normalized first+last name matches
    # an alumni that is ALREADY active under a different identifier - a
    # strong signal of an unnoticed duplicate person worth a manual look,
    # even though the matching rules correctly did not auto-merge them.
    duplicate_candidates_found: int = 0


@dataclass
class _AlumniMatchIndex:
    """In-memory lookup of every alumni already linked to an organization.

    Matching must normalize BOTH the incoming CSV values and the values
    already stored in the database. A plain case-insensitive SQL compare
    fails for LinkedIn URLs that differ only by www., trailing slash,
    query string, or fragment - which is exactly how six rows were being
    recreated on every reimport of the same file.
    """

    by_linkedin: dict[str, Alumni] = field(default_factory=dict)
    by_email: dict[str, Alumni] = field(default_factory=dict)
    by_name: dict[str, Alumni] = field(default_factory=dict)

    def register(self, alumni: Alumni) -> None:
        linkedin_key = _normalize_linkedin_url(alumni.linkedin_url)
        if linkedin_key and linkedin_key not in self.by_linkedin:
            self.by_linkedin[linkedin_key] = alumni

        email_key = _normalize_email(alumni.email)
        if email_key and email_key not in self.by_email:
            self.by_email[email_key] = alumni

        name_key = (
            f"{_normalize_name_part(alumni.first_name)}:{_normalize_name_part(alumni.last_name)}"
        )
        if name_key != ":" and name_key not in self.by_name:
            self.by_name[name_key] = alumni

    def find(
        self,
        linkedin_url: str | None,
        email: str | None,
        first_name: str,
        last_name: str,
    ) -> Alumni | None:
        """Priority: normalized LinkedIn URL > normalized email >
        normalized first+last name.
        """
        if linkedin_url:
            match = self.by_linkedin.get(linkedin_url)
            if match is not None:
                return match

        normalized_email = _normalize_email(email)
        if normalized_email:
            match = self.by_email.get(normalized_email)
            if match is not None:
                return match

        name_key = f"{_normalize_name_part(first_name)}:{_normalize_name_part(last_name)}"
        if name_key != ":":
            match = self.by_name.get(name_key)
            if match is not None:
                return match

        return None


def _build_alumni_match_index(db: Session, organization_id: str) -> _AlumniMatchIndex:
    """Load every alumni linked to this organization (active OR archived)
    into a normalized match index. Archived rows must still match so a
    reimport updates the existing person instead of creating a duplicate.
    """
    index = _AlumniMatchIndex()
    existing_rows = (
        db.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization_id)
        .all()
    )
    for alumni in existing_rows:
        index.register(alumni)
    return index


def _preload_reference_names(db: Session, model, organization_id: str) -> set[str]:
    """Loads every existing reference-table name (Company/Industry/
    University) ONCE per import, so `_get_or_create_reference` below never
    issues a per-row SELECT - the single biggest N+1 pattern in this
    pipeline, and the one that would make a 75k-row import prohibitively
    slow.
    """
    return {
        name.strip().lower()
        for (name,) in db.query(model.name).filter(model.organization_id == organization_id).all()
    }


def _get_or_create_reference(
    db: Session, model, organization_id: str, name: str | None, known_names: set[str], cache: dict
) -> None:
    if not name:
        return
    key = (model, name.strip().lower())
    if key in cache:
        return
    cache[key] = True
    if name.strip().lower() in known_names:
        return
    db.add(model(organization_id=organization_id, name=name))
    known_names.add(name.strip().lower())


def _compute_profile_completion(effective: dict) -> int:
    tracked_fields = [
        "job_title", "company", "industry", "major", "degree", "university",
        "location_original", "linkedin_url", "graduation_year",
    ]
    filled = sum(1 for f in tracked_fields if effective.get(f))
    return round((filled / len(tracked_fields)) * 100)


def import_alumni_csv(
    db: Session,
    organization: Organization,
    file_bytes: bytes,
    imported_by_user_id: str | None = None,
    filename: str | None = None,
) -> ImportSummary:
    summary = ImportSummary()
    summary.filename = filename
    summary.import_logic_version = IMPORT_LOGIC_VERSION
    reference_cache: dict = {}
    # Preloaded ONCE per import (not once per row) to eliminate the N+1
    # SELECT pattern that would otherwise make large (e.g. 75,000-row)
    # imports scale linearly with row count instead of with distinct
    # company/industry/university names.
    known_companies = _preload_reference_names(db, Company, organization.id)
    known_industries = _preload_reference_names(db, Industry, organization.id)
    known_universities = _preload_reference_names(db, University, organization.id)
    company_industry_map = build_company_industry_map(db, organization.id)
    logger.info(
        "CSV replacement import logic %s executing for organization_slug=%s filename=%s",
        IMPORT_LOGIC_VERSION,
        organization.slug,
        filename,
    )

    text = file_bytes.decode("utf-8-sig", errors="replace")

    # --- Physical line accounting (independent of how csv.DictReader
    # parses rows) ---
    # A single trailing blank line (the file ending in a newline) is not
    # counted as a physical data line - that's how every spreadsheet
    # export works, and counting it would over-report by exactly 1.
    physical_lines = text.splitlines()
    if physical_lines and physical_lines[-1].strip() == "":
        physical_lines = physical_lines[:-1]
    summary.csv_physical_lines = len(physical_lines)

    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        summary.row_errors.append({"row": 0, "error": "CSV file has no header row"})
        return summary

    summary.csv_header_rows = 1
    original_headers = list(reader.fieldnames)
    # original header -> normalized alias string (e.g. "Current Job Title" -> "current_job_title")
    header_to_normalized = {h: normalize_header(h) for h in original_headers}
    # normalized alias string -> canonical field (only for recognized aliases)
    header_to_field = {h: ALIAS_TO_FIELD.get(norm) for h, norm in header_to_normalized.items()}

    recognized_headers = [h for h, f_ in header_to_field.items() if f_ is not None]
    unrecognized_headers = [h for h, f_ in header_to_field.items() if f_ is None]
    summary.recognized_headers = recognized_headers
    summary.unrecognized_headers = unrecognized_headers

    logger.info(
        "CSV import header inspection: original_headers=%s normalized_headers=%s "
        "recognized=%s unrecognized=%s",
        original_headers, list(header_to_normalized.values()), recognized_headers, unrecognized_headers,
    )

    # --- Replace-mode bookkeeping ---
    # `touched_alumni_ids` collects every alumni created/updated/confirmed
    # by THIS import; anything previously active but NOT in this set gets
    # archived once the whole file has been processed (see below). This is
    # what makes "the most recently uploaded CSV" the complete, exclusive,
    # authoritative dataset.
    touched_alumni_ids: set[str] = set()
    seen_dedupe_keys: set[str] = set()
    # Match against EVERY alumni ever linked to this organization (including
    # archived ones), with LinkedIn/email/name normalized on BOTH sides so
    # historical URL formatting differences cannot create duplicates.
    match_index = _build_alumni_match_index(db, organization.id)

    rows_parsed = 0
    for row_index, raw_row in enumerate(reader, start=2):  # header is row 1
        rows_parsed += 1
        try:
            # Keyed by normalized alias string (NOT canonical field) so that
            # multiple columns mapping to the same field can be prioritized
            # correctly instead of silently overwriting one another.
            row_by_alias: dict[str, str | None] = {}
            for original_key, value in raw_row.items():
                if original_key is None:
                    continue
                alias = header_to_normalized.get(original_key, normalize_header(original_key))
                cleaned = _clean_value(value)
                # Keep the first nonblank value seen for a given alias key
                # (duplicate header names in a CSV are rare but possible).
                if alias not in row_by_alias or row_by_alias[alias] is None:
                    row_by_alias[alias] = cleaned

            resolved: dict[str, str | None] = {}
            resolved_source: dict[str, str | None] = {}
            for field_name, aliases in FIELD_ALIASES.items():
                value, source = _first_nonblank_with_source(row_by_alias, aliases)
                resolved[field_name] = value
                resolved_source[field_name] = source

            if row_index <= 6:  # first 5 data rows (header is row 1)
                debug_columns = {
                    alias: value
                    for alias, value in row_by_alias.items()
                    if value is not None and any(kw in alias for kw in _DEBUG_KEYWORD_SUBSTRINGS)
                }
                logger.info("CSV import row %s nonblank company/location/education/grad columns: %s",
                            row_index, debug_columns)

            # --- Education column disambiguation ---
            # A generic "Education" column is ambiguous (it could hold an
            # institution name or a degree/credential), so it's resolved by
            # value, not by header name, and only used as a fallback when a
            # more specific university/degree column didn't already supply
            # a value.
            university_raw = resolved.get("university")
            degree_raw = resolved.get("degree")
            university_source = resolved_source.get("university")
            degree_source = resolved_source.get("degree")
            education_raw = row_by_alias.get("education")
            if education_raw:
                education_target = _classify_education_value(education_raw)
                if education_target == "university" and not university_raw:
                    university_raw = education_raw
                    university_source = "education"
                elif education_target == "degree" and not degree_raw:
                    degree_raw = education_raw
                    degree_source = "education"

            # --- Location: separate city/state columns take priority ---
            # Reliable structured columns (City / State) always win over
            # parsing a combined "location" column - never overwrite them
            # with weaker parsed data. The combined column is only used as
            # a fallback when no separate city/state values exist.
            city_raw = resolved.get("city")
            state_raw = resolved.get("state")
            combined_location_raw = resolved.get("location_original")
            constructed_from_city_state = bool(city_raw or state_raw)

            location_fields: dict = {}
            if constructed_from_city_state:
                location_result = normalize_city_state(city_raw, state_raw, state_code_hint=resolved.get("state_code"))
                location_fields = location_result.as_dict()
            elif combined_location_raw:
                location_result = normalize_location(combined_location_raw, db=db)
                location_fields = location_result.as_dict()

            if row_index == 2:
                logger.info(
                    "CSV import first-row inspection: raw_row=%s normalized_row=%s resolved_fields=%s "
                    "resolved_sources=%s",
                    {k: v for k, v in raw_row.items() if k is not None},
                    row_by_alias,
                    resolved,
                    resolved_source,
                )
                logger.info(
                    "CSV import first-row resolved values: company=%r location=%r (constructed_from_city_state=%s) "
                    "city=%r state=%r university=%r degree=%r major=%r graduation_year=%r "
                    "(sources: company=%r location=%r city=%r state=%r university=%r degree=%r "
                    "major=%r graduation_year=%r)",
                    resolved.get("company"), location_fields.get("location_original"), constructed_from_city_state,
                    city_raw, state_raw, university_raw, degree_raw, resolved.get("major"),
                    resolved.get("graduation_year"),
                    resolved_source.get("company"), resolved_source.get("location_original"),
                    resolved_source.get("city"), resolved_source.get("state"), university_source, degree_source,
                    resolved_source.get("major"), resolved_source.get("graduation_year"),
                )
                summary.first_row_original = {k: v for k, v in raw_row.items() if k is not None}
                summary.first_row_normalized = row_by_alias
                # Report the ORIGINAL header text (not the normalized alias)
                # that fed each of these fields, since that's what's
                # actually useful when comparing against the source file.
                normalized_to_original = {norm: orig for orig, norm in header_to_normalized.items()}
                summary.selected_company_column = normalized_to_original.get(resolved_source.get("company"))
                # A combined location column is only "selected" if it was
                # actually used - i.e. no separate city/state took priority.
                summary.selected_location_column = (
                    None if constructed_from_city_state
                    else normalized_to_original.get(resolved_source.get("location_original"))
                )
                summary.selected_city_column = normalized_to_original.get(resolved_source.get("city"))
                summary.selected_state_column = normalized_to_original.get(resolved_source.get("state"))
                summary.selected_university_column = normalized_to_original.get(university_source)
                summary.selected_degree_column = normalized_to_original.get(degree_source)
                summary.selected_major_column = normalized_to_original.get(resolved_source.get("major"))
                summary.selected_graduation_year_column = normalized_to_original.get(
                    resolved_source.get("graduation_year")
                )

            first_name = _normalize_whitespace(resolved.get("first_name"))
            last_name = _normalize_whitespace(resolved.get("last_name"))
            full_name_raw = _normalize_whitespace(resolved.get("full_name"))

            if (not first_name or not last_name) and full_name_raw:
                parts = full_name_raw.split(" ", 1)
                first_name = first_name or parts[0]
                last_name = last_name or (parts[1] if len(parts) > 1 else "")

            if not first_name or not last_name:
                summary.failed += 1
                summary.csv_rows_invalid += 1
                summary.row_errors.append(
                    {"row": row_index, "error": "Missing required field(s): first_name/last_name"}
                )
                continue

            full_name = full_name_raw or f"{first_name} {last_name}".strip()

            graduation_year, grad_year_error = _parse_graduation_year(resolved.get("graduation_year"))
            if grad_year_error:
                summary.row_errors.append({"row": row_index, "error": grad_year_error})

            linkedin_url = _normalize_linkedin_url(resolved.get("linkedin_url"))
            email = resolved.get("email")

            # --- In-file dedupe: never create/update the same person twice
            # from a single upload (e.g. two rows sharing a LinkedIn URL). ---
            dedupe_key = _compute_dedupe_key(linkedin_url, email, first_name, last_name)
            if dedupe_key in seen_dedupe_keys:
                summary.csv_duplicate_rows += 1
                continue
            seen_dedupe_keys.add(dedupe_key)

            job_title = _normalize_whitespace(resolved.get("job_title"))
            company = _normalize_whitespace(resolved.get("company"))

            classification = classify_alumni_fields(
                job_title=job_title,
                company=company,
                existing_industry=resolved.get("industry"),
                existing_career_category=resolved.get("career_category"),
                existing_seniority=resolved.get("seniority"),
                company_industry_map=company_industry_map,
            )
            # Only keep classification keys that actually resolved to a
            # nonblank value - a miss (None) must never overwrite an
            # existing DB value on update. A "*_source" key is only kept
            # alongside its corresponding value key: classify_alumni_fields
            # always returns a source (even "unknown"), so on a row with no
            # job_title/company at all, dropping the orphaned *_source
            # prevents a row that changed NOTHING visible from spuriously
            # registering as "updated" instead of "unchanged".
            classification = {
                k: v
                for k, v in classification.items()
                if v is not None and (not k.endswith("_source") or classification.get(k[: -len("_source")]) is not None)
            }

            raw_verified = resolved.get("verified")
            raw_verification_status = resolved.get("verification_status")
            raw_verification_date = resolved.get("verification_date")
            parsed_verification_date = _parse_date(raw_verification_date)

            existing = match_index.find(
                linkedin_url=linkedin_url,
                email=email,
                first_name=first_name,
                last_name=last_name,
            )

            # field_values only ever contains keys we actually want to
            # write. Blank/unresolved fields are omitted entirely so that
            # (a) on update, the existing nonnull DB value is preserved,
            # and (b) on create, the Alumni model's own column defaults
            # apply (e.g. verification_status="unverified", location
            # status="missing").
            field_values: dict = {
                "first_name": first_name,
                "last_name": last_name,
                "full_name": full_name,
            }
            if graduation_year is not None:
                field_values["graduation_year"] = graduation_year
            if resolved.get("major"):
                field_values["major"] = resolved["major"]
            if degree_raw:
                field_values["degree"] = degree_raw
            if job_title:
                field_values["job_title"] = job_title
            if company:
                field_values["company"] = company
            if linkedin_url:
                field_values["linkedin_url"] = linkedin_url
            if email:
                field_values["email"] = _normalize_email(email)
            if resolved.get("notes"):
                field_values["notes"] = resolved["notes"]
            field_values.update(location_fields)
            field_values.update(classification)

            # University: nonblank CSV value always wins. If blank, apply
            # the fsu-cci-only default, but ONLY to fill a genuine gap
            # (never overwrite an existing nonnull DB value on update).
            if university_raw:
                field_values["university"] = university_raw
            elif organization.slug == FSU_CCI_SLUG and (existing is None or not existing.university):
                field_values["university"] = FSU_CCI_DEFAULT_UNIVERSITY

            if raw_verified is not None:
                verified_bool = _parse_bool(raw_verified)
                field_values["verified"] = verified_bool
                # An explicit "Verification Date" column always wins; only
                # fall back to today's date when the CSV didn't provide one.
                field_values["verification_date"] = (
                    parsed_verification_date
                    if parsed_verification_date is not None
                    else (datetime.now(timezone.utc).date() if verified_bool else None)
                )
                field_values["verification_status"] = raw_verification_status or (
                    "verified" if verified_bool else "unverified"
                )
            else:
                if raw_verification_status is not None:
                    field_values["verification_status"] = raw_verification_status
                if parsed_verification_date is not None:
                    field_values["verification_date"] = parsed_verification_date

            tracked_fields = [
                "job_title", "company", "industry", "major", "degree", "university",
                "location_original", "linkedin_url", "graduation_year",
            ]
            if existing:
                effective = {f: field_values.get(f, getattr(existing, f, None)) for f in tracked_fields}
            else:
                effective = {f: field_values.get(f) for f in tracked_fields}
            field_values["profile_completion"] = _compute_profile_completion(effective)

            if existing:
                changed = False
                for key, value in field_values.items():
                    if getattr(existing, key, None) != value:
                        changed = True
                    setattr(existing, key, value)
                if changed:
                    summary.updated += 1
                else:
                    summary.unchanged += 1
                alumni_id = existing.id
                # Re-register so any newly filled LinkedIn/email on this
                # update is available to later rows in the same file.
                match_index.register(existing)
            else:
                alumni = Alumni(**field_values)
                db.add(alumni)
                db.flush()
                db.add(
                    AlumniOrganization(
                        alumni_id=alumni.id,
                        organization_id=organization.id,
                    )
                )
                summary.created += 1
                alumni_id = alumni.id
                match_index.register(alumni)

                # --- Duplicate-matching audit for this newly created row ---
                strategies_attempted, unmatched_reason = _describe_match_attempt(
                    linkedin_url, email, first_name, last_name
                )
                created_diag = {
                    "row": row_index,
                    "normalized_linkedin_url": linkedin_url,
                    "normalized_email": _normalize_email(email),
                    "normalized_full_name": (
                        f"{_normalize_name_part(first_name)} {_normalize_name_part(last_name)}".strip()
                    ),
                    "matching_strategies_attempted": strategies_attempted,
                    "unmatched_reason": unmatched_reason,
                }
                summary.newly_created_identifiers.append(created_diag)
                logger.info(
                    "CSV import created unmatched row: row=%s normalized_full_name=%r "
                    "normalized_linkedin_url=%r normalized_email=%r "
                    "matching_strategies_attempted=%s unmatched_reason=%s "
                    "import_logic_version=%s",
                    row_index,
                    created_diag["normalized_full_name"],
                    linkedin_url,
                    created_diag["normalized_email"],
                    strategies_attempted,
                    unmatched_reason,
                    IMPORT_LOGIC_VERSION,
                )
                # A same-named ACTIVE alumni that still exists after this
                # creation (i.e. wasn't touched/matched by this import) is a
                # strong signal this "new" row might actually be a duplicate
                # of an existing person under a different identifier.
                name_collision = (
                    db.query(Alumni)
                    .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
                    .filter(
                        AlumniOrganization.organization_id == organization.id,
                        Alumni.is_active.is_(True),
                        Alumni.id != alumni_id,
                        Alumni.first_name.ilike(first_name),
                        Alumni.last_name.ilike(last_name),
                    )
                    .first()
                )
                if name_collision is not None:
                    summary.duplicate_candidates_found += 1

            touched_alumni_ids.add(alumni_id)
            summary.csv_rows_valid += 1

            if row_index == 2:
                logger.info("CSV import first-row mapped Alumni fields: %s", field_values)

            if graduation_year is not None:
                summary.rows_with_graduation_year += 1
            if resolved.get("major"):
                summary.rows_with_major += 1
            if field_values.get("university"):
                summary.rows_with_university += 1
            if job_title:
                summary.rows_with_job_title += 1
            if company:
                summary.rows_with_company += 1
            if location_fields.get("location_original"):
                summary.rows_with_location += 1
            if location_fields.get("city"):
                summary.rows_with_city += 1
            if location_fields.get("state"):
                summary.rows_with_state += 1
            if city_raw:
                summary.rows_with_raw_city += 1
            if state_raw:
                summary.rows_with_raw_state += 1
            if constructed_from_city_state and location_fields.get("location_original"):
                summary.rows_with_constructed_location += 1

            _get_or_create_reference(db, Company, organization.id, company, known_companies, reference_cache)
            _get_or_create_reference(
                db, Industry, organization.id, field_values.get("industry"), known_industries, reference_cache
            )
            _get_or_create_reference(
                db, University, organization.id, field_values.get("university"), known_universities, reference_cache
            )

            # Bounded per-import memory: periodically flush pending
            # inserts/updates rather than holding 75,000 objects in the
            # SQLAlchemy identity map for the entire import.
            if row_index % 500 == 0:
                db.flush()

            record_audit_log(
                db,
                user_id=imported_by_user_id,
                action="update" if existing else "create",
                entity_type="alumni",
                entity_id=alumni_id,
                organization_id=organization.id,
                details={"source": "csv_import", "row": row_index},
            )

        except Exception as exc:  # noqa: BLE001 - row-level isolation is intentional
            summary.failed += 1
            summary.csv_rows_invalid += 1
            summary.row_errors.append({"row": row_index, "error": str(exc)})

    summary.csv_rows_received = rows_parsed
    summary.csv_data_rows = max(summary.csv_physical_lines - summary.csv_header_rows, 0)
    summary.csv_valid_rows = summary.csv_rows_valid
    summary.csv_invalid_rows = summary.csv_rows_invalid

    # Safety net: a genuinely successful "replace mode" import must produce
    # at least one valid alumni row. Zero valid rows out of a nonempty file
    # almost always means a bad/garbled upload, not an intentional "wipe
    # the whole dashboard" action - so it is treated as a failed import and
    # the previous active dataset is preserved untouched, per requirement
    # #10 ("Preserve the previous dataset if the new import fails").
    if rows_parsed > 0 and summary.csv_rows_valid == 0:
        db.rollback()
        summary.status = "failed"
        # Nothing is persisted for a failed attempt (no CSVImport row is
        # written) so the previous successful import unambiguously remains
        # "the most recent CSVImport row" for GET /admin/current-import,
        # and the previous active dataset is left completely untouched.
        logger.error(
            "CSV import FAILED (0 valid rows out of %s parsed) - previous active dataset preserved: "
            "organization_slug=%s organization_id=%s",
            rows_parsed, organization.slug, organization.id,
        )
        raise ValueError(
            "Import produced zero valid alumni rows; aborting to protect the existing active dataset"
        )

    try:
        csv_import_record = CSVImport(
            organization_id=organization.id,
            filename=filename,
            created_count=summary.created,
            updated_count=summary.updated,
            skipped_count=summary.skipped,
            failed_count=summary.failed,
            rows_received=summary.csv_rows_received,
            rows_valid=summary.csv_rows_valid,
            rows_invalid=summary.csv_rows_invalid,
            row_errors_json=json.dumps(summary.row_errors) if summary.row_errors else None,
            imported_by_user_id=imported_by_user_id,
        )
        db.add(csv_import_record)
        db.flush()
        summary.csv_import_id = csv_import_record.id

        # --- REPLACE MODE ---
        # This import's touched alumni become (or remain) the active
        # dataset; everything else previously active for this organization
        # is deactivated (never physically deleted). This makes the most
        # recently uploaded CSV the complete, exclusive, authoritative
        # dataset for the dashboard. is_active/source_import_id live on
        # Alumni itself (not the per-organization link table).
        if touched_alumni_ids:
            db.query(Alumni).filter(Alumni.id.in_(touched_alumni_ids)).update(
                {"is_active": True, "source_import_id": csv_import_record.id}, synchronize_session=False
            )

        org_alumni_ids = [
            row[0]
            for row in db.query(AlumniOrganization.alumni_id)
            .filter(AlumniOrganization.organization_id == organization.id)
            .all()
        ]
        archived_count = 0
        if org_alumni_ids:
            archived_query = db.query(Alumni).filter(
                Alumni.id.in_(org_alumni_ids),
                Alumni.is_active.is_(True),
            )
            if touched_alumni_ids:
                archived_query = archived_query.filter(Alumni.id.notin_(touched_alumni_ids))
            archived_count = archived_query.update({"is_active": False}, synchronize_session=False)
        summary.archived = archived_count

        active_count = (
            db.query(Alumni)
            .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
            .filter(AlumniOrganization.organization_id == organization.id, Alumni.is_active.is_(True))
            .count()
        )
        summary.active_database_total = active_count
        summary.database_total = active_count

        summary.historical_database_total = (
            db.query(AlumniOrganization).filter(AlumniOrganization.organization_id == organization.id).count()
        )

        record_audit_log(
            db,
            user_id=imported_by_user_id,
            action="import",
            entity_type="csv_import",
            entity_id=csv_import_record.id,
            organization_id=organization.id,
            details={
                "created": summary.created,
                "updated": summary.updated,
                "unchanged": summary.unchanged,
                "archived": summary.archived,
                "failed": summary.failed,
                "active_database_total": summary.active_database_total,
                "filename": filename,
            },
        )

        # Shared content-sync versions: bumped as part of THIS SAME
        # transaction (not yet committed) so a rollback below undoes the
        # bump exactly like every other row change. Every logged-in
        # client - regardless of when its session started - can now
        # detect this import via GET /sync/status without polling
        # analytics or alumni data directly.
        bump_for_csv_import(db, updated_by_user_id=imported_by_user_id, resource_id=csv_import_record.id)

        # The whole import (every row change + archiving + the
        # CSVImport/AuditLog rows + content-version bumps) is committed as
        # a single transaction. If the commit itself fails for any reason,
        # the transaction is rolled back so we never report success for a
        # partially-applied import, and the previous active dataset
        # remains untouched.
        db.commit()
    except Exception:
        db.rollback()
        logger.error(
            "CSV import FAILED and was rolled back: organization_slug=%s organization_id=%s "
            "rows_parsed=%s created=%s updated=%s unchanged=%s failed=%s transaction_committed=False",
            organization.slug, organization.id, rows_parsed,
            summary.created, summary.updated, summary.unchanged, summary.failed,
        )
        summary.status = "failed"
        raise

    logger.info(
        "CSV import committed: organization_slug=%s organization_id=%s rows_received=%s rows_valid=%s "
        "rows_invalid=%s duplicate_rows=%s created=%s updated=%s unchanged=%s archived=%s failed=%s "
        "transaction_committed=True active_database_total=%s "
        "rows_with_university=%s rows_with_job_title=%s rows_with_company=%s rows_with_location=%s",
        organization.slug, organization.id, summary.csv_rows_received, summary.csv_rows_valid,
        summary.csv_rows_invalid, summary.csv_duplicate_rows,
        summary.created, summary.updated, summary.unchanged, summary.archived, summary.failed,
        summary.active_database_total, summary.rows_with_university, summary.rows_with_job_title,
        summary.rows_with_company, summary.rows_with_location,
    )
    return summary
