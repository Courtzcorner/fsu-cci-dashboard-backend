"""
Backfill Company.industry (from NULL) and blank Alumni.industry using
ONLY the curated, human-reviewed mappings in
app.services.industry_mapping_data - never AI, never a live web lookup,
never keyword/substring guessing from a company name. See
app.services.industry_backfill_service for the full guardrails.

Defaults to a DRY RUN (no writes) unless --apply is explicitly passed.
An --apply run additionally requires --yes (or an interactive
confirmation) before anything is committed, and prints a full JSON
manifest of exactly what changed - to stdout (between BEGIN/END
markers) AND, if --manifest-output is given, to that file - so it can be
saved even on an ephemeral filesystem (e.g. a Render shell).

Usage (run from the project root, with the venv activated):

    # Safe default - dry run, no writes:
    python scripts/backfill_company_industry.py --organization fsu-stars

    # Same as above, explicit:
    python scripts/backfill_company_industry.py --organization fsu-stars --dry-run

    # Apply (writes), non-interactive, saving the rollback manifest:
    python scripts/backfill_company_industry.py --organization fsu-stars \\
        --apply --yes --manifest-output /tmp/fsu-stars-industry-backfill.json

    # Roll back exactly what an earlier apply run changed:
    python scripts/backfill_company_industry.py --organization fsu-stars \\
        --rollback /path/to/saved-manifest.json --yes
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.services.industry_backfill_service import (  # noqa: E402
    BackfillReport,
    RollbackReport,
    rollback_backfill,
    run_backfill,
)

MANIFEST_BEGIN_MARKER = "===== BEGIN INDUSTRY BACKFILL MANIFEST ====="
MANIFEST_END_MARKER = "===== END INDUSTRY BACKFILL MANIFEST ====="


def _print_report(report: BackfillReport) -> None:
    print(f"[{report.mode.upper()}] organization={report.organization_slug} ({report.organization_id})")
    print(f"  company_rows_examined            : {report.company_rows_examined}")
    print(f"  company_rows_already_classified  : {report.company_rows_already_classified}")
    print(f"  company_mappings_proposed        : {report.company_mappings_proposed}")
    print(f"  alumni_rows_examined             : {report.alumni_rows_examined}")
    print(f"  alumni_records_already_classified: {report.alumni_records_already_classified}")
    print(f"  alumni_records_classified        : {report.alumni_records_classified}")
    print(f"  invalid_employer_values_skipped  : {report.invalid_employer_values_skipped}")
    print(f"  unknown_companies_skipped        : {report.unknown_companies_skipped}")
    print(f"  alumni_without_company_skipped   : {report.alumni_without_company_skipped}")
    print("  proposed_industry_counts:")
    if report.proposed_industry_counts:
        for industry, count in sorted(report.proposed_industry_counts.items(), key=lambda item: (-item[1], item[0])):
            print(f"    {industry}: {count}")
    else:
        print("    (none)")
    suffix = " (truncated - use --show-all-unknown to see the rest)" if report.unknown_employer_names_truncated else ""
    print(f"  unknown_employer_names{suffix}:")
    if report.unknown_employer_names:
        for entry in report.unknown_employer_names:
            print(f"    {entry['name']}: {entry['count']}")
    else:
        print("    (none)")


def _print_manifest(manifest: dict) -> None:
    print(MANIFEST_BEGIN_MARKER)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    print(MANIFEST_END_MARKER)


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    response = input(f"{prompt} [y/N]: ").strip().lower()
    return response in ("y", "yes")


def _run_backfill_command(db, organization: Organization, args: argparse.Namespace) -> int:
    apply_mode = bool(args.apply)
    try:
        report = run_backfill(
            db,
            organization,
            apply=apply_mode,
            unknown_employer_limit=args.unknown_limit,
            show_all_unknown=args.show_all_unknown,
        )
    except Exception:
        db.rollback()
        raise

    _print_report(report)

    if not apply_mode:
        db.rollback()  # defensive - no writes should exist yet, but never leave a dangling transaction
        print("\nDRY RUN - no changes were written. Re-run with --apply to write these changes.")
        return 0

    print(
        f"\nAbout to WRITE {report.company_mappings_proposed} Company row(s) and "
        f"{report.alumni_records_classified} Alumni row(s) for organization "
        f"'{organization.slug}'."
    )
    if not _confirm("Proceed and commit these changes?", args.yes):
        db.rollback()
        print("Aborted - no changes were written.")
        return 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    _print_manifest(report.manifest)
    if args.manifest_output:
        Path(args.manifest_output).write_text(json.dumps(report.manifest, indent=2, sort_keys=True))
        print(f"\nManifest also written to: {args.manifest_output}")
    print("\nAPPLIED - changes committed.")
    return 0


def _print_rollback_report(report: RollbackReport) -> None:
    print(f"[ROLLBACK] organization={report.organization_slug} ({report.organization_id})")
    print(f"  companies_in_manifest: {report.companies_in_manifest}")
    print(f"  companies_reverted   : {report.companies_reverted}")
    print(f"  alumni_in_manifest   : {report.alumni_in_manifest}")
    print(f"  alumni_reverted      : {report.alumni_reverted}")


def _run_rollback_command(db, organization: Organization, args: argparse.Namespace) -> int:
    manifest_path = Path(args.rollback)
    if not manifest_path.exists():
        print(f"Manifest file not found: {manifest_path}")
        return 1
    manifest = json.loads(manifest_path.read_text())

    print(
        f"About to ROLL BACK {len(manifest.get('companies_changed', []))} Company row(s) and "
        f"{len(manifest.get('alumni_changed', []))} Alumni row(s) for organization "
        f"'{organization.slug}', using manifest '{manifest_path}'."
    )
    if not _confirm("Proceed and commit this rollback?", args.yes):
        print("Aborted - no changes were written.")
        return 1

    try:
        report = rollback_backfill(db, organization, manifest)
        db.commit()
    except Exception:
        db.rollback()
        raise

    _print_rollback_report(report)
    print("\nROLLED BACK - changes committed.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill Company.industry / blank Alumni.industry from curated, reviewed mappings only."
    )
    parser.add_argument("--organization", required=True, help="Organization slug to operate on (required)")
    parser.add_argument("--dry-run", action="store_true", help="Explicit dry run (default behavior; no writes)")
    parser.add_argument("--apply", action="store_true", help="Write changes (default is dry run without this flag)")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    parser.add_argument("--manifest-output", help="Path to also write the rollback manifest JSON to, on --apply")
    parser.add_argument("--rollback", help="Path to a previously saved manifest JSON to roll back")
    parser.add_argument(
        "--unknown-limit", type=int, default=20, help="Max distinct unknown employer names to report (default 20)"
    )
    parser.add_argument(
        "--show-all-unknown", action="store_true", help="Show every distinct unknown employer name, not just the top N"
    )
    args = parser.parse_args()

    if args.apply and args.rollback:
        print("Error: --apply and --rollback are mutually exclusive.")
        sys.exit(2)

    db = SessionLocal()
    try:
        organization = db.query(Organization).filter(Organization.slug == args.organization).first()
        if organization is None:
            print(f"No organization found with slug '{args.organization}'")
            sys.exit(1)

        if args.rollback:
            exit_code = _run_rollback_command(db, organization, args)
        else:
            exit_code = _run_backfill_command(db, organization, args)
    finally:
        db.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
