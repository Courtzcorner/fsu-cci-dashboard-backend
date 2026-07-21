"""
Safely re-run the alumni CSV import against an already-imported file.

This uses the exact same `import_alumni_csv` pipeline as
`POST /admin/import-alumni`, so it is safe to run repeatedly against the
same file: existing records are matched (LinkedIn URL, then
first/last/graduation_year) and updated in place - never duplicated - and
any field that is blank in this CSV will never overwrite a nonnull value
already in the database. Re-running is exactly how you "backfill" fields
that were null because of a previous header-mapping bug: once the mapping
is fixed, reimporting the same file fills in the previously-null fields.

Usage (run from the project root, with the venv activated):

    python scripts/reimport_alumni_csv.py --organization fsu-cci --file alumni.csv
    python scripts/reimport_alumni_csv.py --organization fsu-cci --file alumni.csv --imported-by admin
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.csv_import_service import import_alumni_csv  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely reimport an alumni CSV file.")
    parser.add_argument("--organization", required=True, help="Organization slug, e.g. fsu-cci")
    parser.add_argument("--file", required=True, help="Path to the CSV file to (re)import")
    parser.add_argument(
        "--imported-by", help="Username to attribute this import to in AuditLog/CSVImport (optional)"
    )
    args = parser.parse_args()

    csv_path = Path(args.file)
    if not csv_path.is_file():
        print(f"No such file: {csv_path}")
        sys.exit(1)

    db = SessionLocal()
    try:
        organization = db.query(Organization).filter(Organization.slug == args.organization).first()
        if organization is None:
            print(f"No organization found with slug '{args.organization}'")
            sys.exit(1)

        imported_by_user_id = None
        if args.imported_by:
            user = db.query(User).filter(User.username == args.imported_by).first()
            if user is None:
                print(f"No user found with username '{args.imported_by}' (continuing without attribution)")
            else:
                imported_by_user_id = user.id

        file_bytes = csv_path.read_bytes()
        summary = import_alumni_csv(
            db,
            organization,
            file_bytes,
            imported_by_user_id=imported_by_user_id,
            filename=csv_path.name,
        )

        print(f"Reimport complete for organization={organization.slug} file={csv_path.name}")
        print(f"  created           : {summary.created}")
        print(f"  updated           : {summary.updated}")
        print(f"  failed            : {summary.failed}")
        print(f"  database_total    : {summary.database_total}")
        print(f"  recognized_headers   : {summary.recognized_headers}")
        print(f"  unrecognized_headers : {summary.unrecognized_headers}")
        print(f"  rows_with_university : {summary.rows_with_university}")
        print(f"  rows_with_job_title  : {summary.rows_with_job_title}")
        print(f"  rows_with_company    : {summary.rows_with_company}")
        print(f"  rows_with_location   : {summary.rows_with_location}")
        if summary.row_errors:
            print(f"  row_errors ({len(summary.row_errors)}):")
            for err in summary.row_errors[:20]:
                print(f"    row {err['row']}: {err['error']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
