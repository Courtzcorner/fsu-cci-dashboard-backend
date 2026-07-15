from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, get_current_user, require_admin_role
from app.models.organization import Organization
from app.schemas.admin import ImportResult, NormalizeLocationsResult, RowError
from app.services.csv_import_service import import_alumni_csv
from app.services.location_reprocess_service import reprocess_locations

router = APIRouter(prefix="/admin", tags=["admin"])


def _resolve_organization(db: Session, slug: str) -> Organization:
    organization = db.query(Organization).filter(Organization.slug == slug).first()
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return organization


@router.post("/import-alumni", response_model=ImportResult)
async def import_alumni(
    organization_slug: str = Form(..., alias="organization"),
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportResult:
    organization = _resolve_organization(db, organization_slug)
    require_admin_role(current_user)

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .csv files are accepted")

    contents = await file.read()
    summary = import_alumni_csv(db, organization, contents)

    return ImportResult(
        organization=organization.slug,
        created=summary.created,
        updated=summary.updated,
        skipped=summary.skipped,
        failed=summary.failed,
        row_errors=[RowError(**e) for e in summary.row_errors],
    )


@router.post("/normalize-locations", response_model=NormalizeLocationsResult)
def normalize_locations(
    organization_slug: str | None = Form(default=None, alias="organization"),
    dry_run: bool = Form(default=False),
    batch_size: int = Form(default=200),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NormalizeLocationsResult:
    """Synchronous reprocessing endpoint suitable for small/medium
    datasets. For very large datasets prefer the CLI script
    (scripts/normalize_existing_locations.py) run out-of-band.
    """
    require_admin_role(current_user)
    organization = _resolve_organization(db, organization_slug) if organization_slug else None

    result = reprocess_locations(
        db,
        organization_id=organization.id if organization else None,
        dry_run=dry_run,
        batch_size=batch_size,
    )

    return NormalizeLocationsResult(
        organization=organization.slug if organization else None,
        processed=result["processed"],
        updated=result["updated"],
        unchanged=result["unchanged"],
        dry_run=dry_run,
    )
