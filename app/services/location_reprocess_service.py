"""
Shared logic for reprocessing `location_original` -> normalized fields on
existing alumni records. Used by both `scripts/normalize_existing_locations.py`
and the `POST /admin/normalize-locations` endpoint so behavior never drifts
between the two entry points.
"""
from dataclasses import asdict

from sqlalchemy.orm import Session

from app.models.alumni import Alumni
from app.services.location_normalization_service import normalize_location

NORMALIZED_FIELDS = [
    "city", "state", "state_code", "country", "metro_area", "display_location",
    "latitude", "longitude", "location_normalization_status",
]


def reprocess_locations(
    db: Session,
    organization_id: str | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
) -> dict:
    """Recompute normalized location fields for existing alumni, preserving
    `location_original`. Skips writing rows whose normalized output is
    unchanged. Processes in batches to bound memory usage on large tables.
    """
    query = db.query(Alumni)
    if organization_id:
        from app.models.alumni import AlumniOrganization

        query = query.join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id).filter(
            AlumniOrganization.organization_id == organization_id
        )

    processed = 0
    updated = 0
    unchanged = 0

    offset = 0
    while True:
        batch = query.order_by(Alumni.id).offset(offset).limit(batch_size).all()
        if not batch:
            break

        for record in batch:
            processed += 1
            result = normalize_location(record.location_original, db=db)
            new_values = asdict(result)
            new_values.pop("location_original", None)

            changed = any(getattr(record, field) != new_values[field] for field in NORMALIZED_FIELDS)
            if changed:
                updated += 1
                if not dry_run:
                    for field in NORMALIZED_FIELDS:
                        setattr(record, field, new_values[field])
            else:
                unchanged += 1

        offset += batch_size

    if not dry_run:
        db.commit()
    else:
        db.rollback()

    return {"processed": processed, "updated": updated, "unchanged": unchanged}
