"""
Reprocess normalized location fields for existing alumni records.

`location_original` is always preserved; only the derived fields (city,
state, state_code, country, metro_area, display_location, lat/lng, status)
are recomputed.

Usage (run from the project root, with the venv activated):

    python scripts/normalize_existing_locations.py --organization fsu-cci --dry-run
    python scripts/normalize_existing_locations.py --organization fsu-cci
    python scripts/normalize_existing_locations.py --organization fsu-cci --batch-size 100
    python scripts/normalize_existing_locations.py            # all organizations
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.services.location_reprocess_service import reprocess_locations  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Reprocess alumni location normalization.")
    parser.add_argument("--organization", help="Organization slug to limit reprocessing to")
    parser.add_argument("--dry-run", action="store_true", help="Compute changes without writing them")
    parser.add_argument("--batch-size", type=int, default=200, help="Rows processed per DB batch")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        organization_id = None
        if args.organization:
            organization = db.query(Organization).filter(Organization.slug == args.organization).first()
            if organization is None:
                print(f"No organization found with slug '{args.organization}'")
                sys.exit(1)
            organization_id = organization.id

        result = reprocess_locations(
            db,
            organization_id=organization_id,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
        )

        mode = "DRY RUN" if args.dry_run else "APPLIED"
        scope = args.organization or "ALL ORGANIZATIONS"
        print(f"[{mode}] scope={scope} batch_size={args.batch_size}")
        print(f"  processed : {result['processed']}")
        print(f"  updated   : {result['updated']}")
        print(f"  unchanged : {result['unchanged']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
