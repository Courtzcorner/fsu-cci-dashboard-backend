"""
Clean up EXISTING placeholder employer values (e.g. "Not stated", "N/A",
"not stated", "Not Stated") already stored in `Alumni.company` and any
matching organization-scoped `Company` reference row - see
app.services.company_placeholder_policy for the reviewed, centralized
placeholder list, and app.services.company_placeholder_cleanup_service
for the underlying logic and guardrails.

Defaults to a DRY RUN (no writes) unless --apply is explicitly passed. An
--apply run additionally requires --yes (or an interactive confirmation)
before anything is committed, and prints a full JSON manifest of exactly
what changed - to stdout (between BEGIN/END markers) AND, if
--manifest-output is given, to that file.

Usage (run from the project root, with the venv activated):

    # Safe default - dry run, no writes:
    python scripts/cleanup_placeholder_company_values.py --organization stars-national

    # Same as above, explicit:
    python scripts/cleanup_placeholder_company_values.py --organization stars-national --dry-run

    # Apply (writes), non-interactive, saving the rollback manifest:
    python scripts/cleanup_placeholder_company_values.py --organization stars-national \\
        --apply --yes --manifest-output /tmp/stars-national-placeholder-cleanup.json

    # Roll back exactly what an earlier apply run changed:
    python scripts/cleanup_placeholder_company_values.py --organization stars-national \\
        --rollback /path/to/saved-manifest.json --yes
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.services.company_placeholder_cleanup_service import (  # noqa: E402
    CleanupReport,
    RollbackReport,
    rollback_cleanup,
    run_cleanup,
)

MANIFEST_BEGIN_MARKER = "===== BEGIN PLACEHOLDER CLEANUP MANIFEST ====="
MANIFEST_END_MARKER = "===== END PLACEHOLDER CLEANUP MANIFEST ====="


def _print_report(report: CleanupReport) -> None:
    print(f"[{report.mode.upper()}] organization={report.organization_slug} ({report.organization_id})")
    print(f"  alumni_rows_examined          : {report.alumni_rows_examined}")
    print(f"  alumni_rows_with_placeholder  : {report.alumni_rows_with_placeholder}")
    print(f"  company_rows_examined         : {report.company_rows_examined}")
    print(f"  company_rows_with_placeholder : {report.company_rows_with_placeholder}")
    print("  affected alumni rows, grouped by original stored value:")
    if report.affected_alumni_counts_by_original_value:
        for value, count in sorted(
            report.affected_alumni_counts_by_original_value.items(), key=lambda item: (-item[1], item[0])
        ):
            print(f"    {value!r}: {count}")
    else:
        print("    (none)")
    print("  affected Company reference rows, grouped by original stored value:")
    if report.affected_company_counts_by_original_value:
        for value, count in sorted(
            report.affected_company_counts_by_original_value.items(), key=lambda item: (-item[1], item[0])
        ):
            print(f"    {value!r}: {count}")
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


def _run_cleanup_command(db, organization: Organization, args: argparse.Namespace) -> int:
    apply_mode = bool(args.apply)
    try:
        report = run_cleanup(db, organization, apply=apply_mode)
    except Exception:
        db.rollback()
        raise

    _print_report(report)

    if not apply_mode:
        db.rollback()  # defensive - no writes should exist yet, but never leave a dangling transaction
        print("\nDRY RUN - no changes were written. Re-run with --apply to write these changes.")
        return 0

    print(
        f"\nAbout to WRITE {report.alumni_rows_with_placeholder} Alumni row(s) (company -> NULL) and DELETE "
        f"{report.company_rows_with_placeholder} Company reference row(s) for organization "
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
    print(f"  alumni_in_manifest    : {report.alumni_in_manifest}")
    print(f"  alumni_reverted       : {report.alumni_reverted}")
    print(f"  companies_in_manifest : {report.companies_in_manifest}")
    print(f"  companies_recreated   : {report.companies_recreated}")


def _run_rollback_command(db, organization: Organization, args: argparse.Namespace) -> int:
    manifest_path = Path(args.rollback)
    if not manifest_path.exists():
        print(f"Manifest file not found: {manifest_path}")
        return 1
    manifest = json.loads(manifest_path.read_text())

    print(
        f"About to ROLL BACK {len(manifest.get('alumni_changed', []))} Alumni row(s) and recreate "
        f"{len(manifest.get('companies_changed', []))} Company row(s) for organization "
        f"'{organization.slug}', using manifest '{manifest_path}'."
    )
    if not _confirm("Proceed and commit this rollback?", args.yes):
        print("Aborted - no changes were written.")
        return 1

    try:
        report = rollback_cleanup(db, organization, manifest)
        db.commit()
    except Exception:
        db.rollback()
        raise

    _print_rollback_report(report)
    print("\nROLLED BACK - changes committed.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean up placeholder employer values (e.g. 'Not stated') from existing Alumni/Company data."
    )
    parser.add_argument("--organization", required=True, help="Organization slug to operate on (required)")
    parser.add_argument("--dry-run", action="store_true", help="Explicit dry run (default behavior; no writes)")
    parser.add_argument("--apply", action="store_true", help="Write changes (default is dry run without this flag)")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    parser.add_argument("--manifest-output", help="Path to also write the rollback manifest JSON to, on --apply")
    parser.add_argument("--rollback", help="Path to a previously saved manifest JSON to roll back")
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
            exit_code = _run_cleanup_command(db, organization, args)
    finally:
        db.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
