"""
CSV import pipeline for alumni records, scoped to a single organization.

Responsibilities:
- normalize column names / whitespace / blank -> null
- preserve `location_original` and normalize location via
  `location_normalization_service`
- normalize LinkedIn URLs
- parse graduation years
- validate required fields
- detect + update duplicates (never merge purely on name)
- return created/updated/skipped/failed counts and row-specific errors
"""
import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.models.alumni import Alumni, AlumniOrganization
from app.models.audit import CSVImport
from app.models.organization import Organization
from app.models.reference import Company, Industry, University
from app.services.audit_service import record_audit_log
from app.services.classification_service import classify_alumni_fields
from app.services.location_normalization_service import normalize_location

REQUIRED_FIELDS = {"first_name", "last_name"}

# Accepts common header variants and maps them to our canonical column
# names. Matching is done after lowercasing + stripping punctuation/spaces.
COLUMN_ALIASES: dict[str, str] = {
    "firstname": "first_name",
    "first": "first_name",
    "lastname": "last_name",
    "last": "last_name",
    "fullname": "full_name",
    "name": "full_name",
    "gradyear": "graduation_year",
    "graduationyear": "graduation_year",
    "classyear": "graduation_year",
    "major": "major",
    "degree": "degree",
    "university": "university",
    "school": "university",
    "jobtitle": "job_title",
    "title": "job_title",
    "position": "job_title",
    "company": "company",
    "employer": "company",
    "industry": "industry",
    "careercategory": "career_category",
    "career": "career_category",
    "seniority": "seniority",
    "seniority level": "seniority",
    "seniioritylevel": "seniority",
    "location": "location_original",
    "city_state": "location_original",
    "linkedin": "linkedin_url",
    "linkedinurl": "linkedin_url",
    "linkedinprofile": "linkedin_url",
    "verified": "verified",
    "email": "email",
}


def _normalize_column_name(raw: str) -> str:
    key = raw.strip().lower()
    key = re.sub(r"[\s_\-]+", "", key)
    return COLUMN_ALIASES.get(key, re.sub(r"[\s\-]+", "_", raw.strip().lower()))


def _clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


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


def _compute_profile_completion(row: dict) -> int:
    tracked_fields = [
        "job_title", "company", "industry", "major", "degree", "university",
        "location_original", "linkedin_url", "graduation_year",
    ]
    filled = sum(1 for f in tracked_fields if row.get(f))
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

    normalized_fieldnames = {original: _normalize_column_name(original) for original in reader.fieldnames}

    rows_parsed = 0
    for row_index, raw_row in enumerate(reader, start=2):  # header is row 1
        rows_parsed += 1
        try:
            row: dict[str, str | None] = {}
            for original_key, value in raw_row.items():
                if original_key is None:
                    continue
                canonical_key = normalized_fieldnames.get(original_key, original_key)
                row[canonical_key] = _clean_value(value)

            first_name = row.get("first_name")
            last_name = row.get("last_name")
            full_name = row.get("full_name")

            if (not first_name or not last_name) and full_name:
                parts = full_name.split(" ", 1)
                first_name = first_name or parts[0]
                last_name = last_name or (parts[1] if len(parts) > 1 else "")

            if not first_name or not last_name:
                summary.failed += 1
                summary.row_errors.append(
                    {"row": row_index, "error": "Missing required field(s): first_name/last_name"}
                )
                continue

            full_name = full_name or f"{first_name} {last_name}".strip()

            graduation_year, grad_year_error = _parse_graduation_year(row.get("graduation_year"))
            if grad_year_error:
                summary.row_errors.append({"row": row_index, "error": grad_year_error})

            linkedin_url = _normalize_linkedin_url(row.get("linkedin_url"))
            location_original = row.get("location_original")
            location_result = normalize_location(location_original, db=db)

            classification = classify_alumni_fields(
                job_title=row.get("job_title"),
                company=row.get("company"),
                existing_industry=row.get("industry"),
                existing_career_category=row.get("career_category"),
                existing_seniority=row.get("seniority"),
            )

            verified = _parse_bool(row.get("verified"))

            existing = _find_existing_alumni(
                db,
                organization_id=organization.id,
                linkedin_url=linkedin_url,
                email=row.get("email"),
                first_name=first_name,
                last_name=last_name,
                graduation_year=graduation_year,
            )

            field_values = dict(
                first_name=first_name,
                last_name=last_name,
                full_name=full_name,
                graduation_year=graduation_year,
                major=row.get("major"),
                degree=row.get("degree"),
                university=row.get("university"),
                job_title=row.get("job_title"),
                company=row.get("company"),
                linkedin_url=linkedin_url,
                verified=verified,
                verification_status="verified" if verified else "unverified",
                verification_date=datetime.now(timezone.utc).date() if verified else None,
                **classification,
                **location_result.as_dict(),
            )
            field_values["profile_completion"] = _compute_profile_completion(field_values)

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

            _get_or_create_reference(db, Company, organization.id, row.get("company"), reference_cache)
            _get_or_create_reference(db, Industry, organization.id, field_values.get("industry"), reference_cache)
            _get_or_create_reference(db, University, organization.id, row.get("university"), reference_cache)

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
        "created=%s updated=%s skipped=%s failed=%s transaction_committed=True database_total=%s",
        organization.slug, organization.id, rows_parsed,
        summary.created, summary.updated, summary.skipped, summary.failed,
        summary.database_total,
    )
    return summary
