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
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.alumni import Alumni, AlumniOrganization
from app.models.audit import CSVImport
from app.models.organization import Organization
from app.models.reference import Company, Industry, University
from app.services.audit_service import record_audit_log
from app.services.classification_service import classify_alumni_fields
from app.services.location_normalization_service import normalize_city_state, normalize_location

logger = logging.getLogger(__name__)

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
    "first_name": ["first_name", "firstname", "first", "student_firstname", "student_first_name"],
    "last_name": ["last_name", "lastname", "last", "student_lastname", "student_last_name"],
    "full_name": ["full_name", "name", "student_name", "alumni_name"],
    "verification_status": ["verification_status", "verified_status", "education_match_status"],
    "verified": ["verified", "is_verified"],
    # Recognized (won't be flagged as "unrecognized") but not yet persisted
    # to an Alumni column - no schema field exists for these today.
    "profile_headline": ["profile_headline", "headline", "linkedin_headline"],
    "employment_tenure": ["employment_tenure", "tenure"],
    "employment_type": ["employment_type", "job_type"],
    "email": ["email"],
}

# Note: "profile_headline", "employment_tenure", "employment_type", and
# "email" are recognized (won't show up as unrecognized_headers) but have
# no backing Alumni column yet, so they are intentionally never added to
# field_values below.

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
    if not value:
        return None
    url = value.strip()
    if not url:
        return None
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"
    url = url.rstrip("/")
    # Collapse http -> https and strip tracking query strings for stable dedup matching.
    url = url.replace("http://", "https://")
    url = url.split("?")[0]
    return url


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


@dataclass
class RowOutcome:
    action: str  # "created" | "updated" | "skipped" | "failed"
    error: str | None = None


@dataclass
class ImportSummary:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    row_errors: list[dict] = field(default_factory=list)
    csv_import_id: str | None = None
    database_total: int = 0
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


def _find_existing_alumni(
    db: Session,
    organization_id: str,
    linkedin_url: str | None,
    email: str | None,
    first_name: str,
    last_name: str,
    graduation_year: int | None,
) -> Alumni | None:
    """Duplicate matching priority: LinkedIn URL > email > (first, last,
    graduation_year, organization). Never match on name alone.
    """
    base_query = (
        db.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization_id)
    )

    if linkedin_url:
        match = base_query.filter(Alumni.linkedin_url == linkedin_url).first()
        if match:
            return match

    # `email` is not yet a persisted column (spec: "email if later
    # available") - reserved for when that column is added to the schema.

    if graduation_year is not None:
        match = base_query.filter(
            Alumni.first_name.ilike(first_name),
            Alumni.last_name.ilike(last_name),
            Alumni.graduation_year == graduation_year,
        ).first()
        if match:
            return match

    return None


def _get_or_create_reference(db: Session, model, organization_id: str, name: str | None, cache: dict) -> None:
    if not name:
        return
    key = (model, name.strip().lower())
    if key in cache:
        return
    existing = db.query(model).filter(model.organization_id == organization_id, model.name == name).first()
    if existing is None:
        db.add(model(organization_id=organization_id, name=name))
    cache[key] = True


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
    reference_cache: dict = {}

    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        summary.row_errors.append({"row": 0, "error": "CSV file has no header row"})
        return summary

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

            first_name = resolved.get("first_name")
            last_name = resolved.get("last_name")
            full_name_raw = resolved.get("full_name")

            if (not first_name or not last_name) and full_name_raw:
                parts = full_name_raw.split(" ", 1)
                first_name = first_name or parts[0]
                last_name = last_name or (parts[1] if len(parts) > 1 else "")

            if not first_name or not last_name:
                summary.failed += 1
                summary.row_errors.append(
                    {"row": row_index, "error": "Missing required field(s): first_name/last_name"}
                )
                continue

            full_name = full_name_raw or f"{first_name} {last_name}".strip()

            graduation_year, grad_year_error = _parse_graduation_year(resolved.get("graduation_year"))
            if grad_year_error:
                summary.row_errors.append({"row": row_index, "error": grad_year_error})

            linkedin_url = _normalize_linkedin_url(resolved.get("linkedin_url"))

            job_title = resolved.get("job_title")
            company = resolved.get("company")

            classification = classify_alumni_fields(
                job_title=job_title,
                company=company,
                existing_industry=resolved.get("industry"),
                existing_career_category=resolved.get("career_category"),
                existing_seniority=resolved.get("seniority"),
            )
            # Only keep classification keys that actually resolved to a
            # nonblank value - a miss (None) must never overwrite an
            # existing DB value on update.
            classification = {k: v for k, v in classification.items() if v is not None}

            raw_verified = resolved.get("verified")
            raw_verification_status = resolved.get("verification_status")

            existing = _find_existing_alumni(
                db,
                organization_id=organization.id,
                linkedin_url=linkedin_url,
                email=resolved.get("email"),
                first_name=first_name,
                last_name=last_name,
                graduation_year=graduation_year,
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
                field_values["verification_date"] = datetime.now(timezone.utc).date() if verified_bool else None
                field_values["verification_status"] = raw_verification_status or (
                    "verified" if verified_bool else "unverified"
                )
            elif raw_verification_status is not None:
                field_values["verification_status"] = raw_verification_status

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
                for key, value in field_values.items():
                    setattr(existing, key, value)
                summary.updated += 1
                alumni_id = existing.id
            else:
                alumni = Alumni(**field_values)
                db.add(alumni)
                db.flush()
                db.add(AlumniOrganization(alumni_id=alumni.id, organization_id=organization.id))
                summary.created += 1
                alumni_id = alumni.id

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

            _get_or_create_reference(db, Company, organization.id, company, reference_cache)
            _get_or_create_reference(db, Industry, organization.id, field_values.get("industry"), reference_cache)
            _get_or_create_reference(db, University, organization.id, field_values.get("university"), reference_cache)

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
            summary.row_errors.append({"row": row_index, "error": str(exc)})

    try:
        csv_import_record = CSVImport(
            organization_id=organization.id,
            filename=filename,
            created_count=summary.created,
            updated_count=summary.updated,
            skipped_count=summary.skipped,
            failed_count=summary.failed,
            row_errors_json=json.dumps(summary.row_errors) if summary.row_errors else None,
            imported_by_user_id=imported_by_user_id,
        )
        db.add(csv_import_record)
        db.flush()
        summary.csv_import_id = csv_import_record.id

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
                "failed": summary.failed,
                "filename": filename,
            },
        )

        # The whole import (every row change + the CSVImport/AuditLog rows)
        # is committed as a single transaction. If the commit itself fails
        # for any reason, the transaction is rolled back so we never report
        # success for a partially-applied import.
        db.commit()
    except Exception:
        db.rollback()
        logger.error(
            "CSV import FAILED and was rolled back: organization_slug=%s organization_id=%s "
            "rows_parsed=%s created=%s updated=%s skipped=%s failed=%s transaction_committed=False",
            organization.slug, organization.id, rows_parsed,
            summary.created, summary.updated, summary.skipped, summary.failed,
        )
        raise

    # Re-query the database (not in-memory counters) to confirm the import
    # actually persisted, and report the organization's true current total.
    summary.database_total = (
        db.query(AlumniOrganization).filter(AlumniOrganization.organization_id == organization.id).count()
    )

    logger.info(
        "CSV import committed: organization_slug=%s organization_id=%s rows_parsed=%s "
        "created=%s updated=%s skipped=%s failed=%s transaction_committed=True database_total=%s "
        "rows_with_university=%s rows_with_job_title=%s rows_with_company=%s rows_with_location=%s",
        organization.slug, organization.id, rows_parsed,
        summary.created, summary.updated, summary.skipped, summary.failed,
        summary.database_total, summary.rows_with_university, summary.rows_with_job_title,
        summary.rows_with_company, summary.rows_with_location,
    )
    return summary
