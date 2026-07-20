import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.database import get_db
from app.deps import CurrentUser, get_current_user, require_admin_role
from app.models.alumni import Alumni
from app.models.legal_name import LegalNameChangeRequest
from app.models.organization import Organization
from app.schemas.admin import ImportResult, NormalizeLocationsResult, RowError
from app.schemas.profile import LegalNameChangeRequestOut
from app.services.audit_service import record_audit_log
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
    organization: str = Form(default="fsu-cci"),
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportResult:
    # Admin role is checked first, then the requested organization slug is
    # authorized/resolved against the database - the submitted form field
    # alone never grants access to an organization that doesn't exist or
    # that this deployment doesn't manage.
    require_admin_role(current_user)
    organization_record = _resolve_organization(db, organization)

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .csv files are accepted")

    contents = await file.read()

    try:
        summary = import_alumni_csv(
            db, organization_record, contents, imported_by_user_id=current_user.id, filename=file.filename
        )
    except Exception:
        # import_alumni_csv already rolled back its own transaction on
        # failure; nothing was committed, so no partial import is visible
        # to anyone. Never report success for a failed import.
        logger.exception(
            "CSV import failed for organization=%s; transaction rolled back", organization_record.slug
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Import failed and was rolled back. No records were changed.",
        )

    return ImportResult(
        organization=organization_record.slug,
        created=summary.created,
        updated=summary.updated,
        skipped=summary.skipped,
        failed=summary.failed,
        database_total=summary.database_total,
        row_errors=[RowError(**e) for e in summary.row_errors],
        csv_import_id=summary.csv_import_id,
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


# --------------------------------------------------------------------------
# Legal name change request review
# --------------------------------------------------------------------------


@router.get("/legal-name-requests", response_model=list[LegalNameChangeRequestOut])
def list_legal_name_requests(
    status_filter: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LegalNameChangeRequest]:
    require_admin_role(current_user)
    query = db.query(LegalNameChangeRequest)
    if status_filter:
        query = query.filter(LegalNameChangeRequest.status == status_filter)
    return query.order_by(LegalNameChangeRequest.created_at.asc()).all()


def _resolve_pending_request(db: Session, request_id: str) -> LegalNameChangeRequest:
    request = db.get(LegalNameChangeRequest, request_id)
    if request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Legal name change request not found")
    if request.status != "pending_review":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request has already been reviewed")
    return request


@router.post("/legal-name-requests/{request_id}/approve", response_model=LegalNameChangeRequestOut)
def approve_legal_name_request(
    request_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LegalNameChangeRequest:
    require_admin_role(current_user)
    request = _resolve_pending_request(db, request_id)

    alumni = db.get(Alumni, request.alumni_id)
    if alumni is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alumni record not found")

    now = datetime.now(timezone.utc)
    alumni.verified_legal_name = request.requested_legal_name
    alumni.legal_name_verified = True
    alumni.legal_name_verification_status = "verified"
    alumni.legal_name_verified_at = now.date()

    request.status = "approved"
    request.reviewed_by_user_id = current_user.id
    request.reviewed_at = now

    record_audit_log(
        db, user_id=current_user.id, action="approve", entity_type="legal_name_change_request",
        entity_id=request.id, details={"alumni_id": alumni.id},
    )
    db.commit()
    db.refresh(request)
    return request


@router.post("/legal-name-requests/{request_id}/reject", response_model=LegalNameChangeRequestOut)
def reject_legal_name_request(
    request_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LegalNameChangeRequest:
    require_admin_role(current_user)
    request = _resolve_pending_request(db, request_id)

    alumni = db.get(Alumni, request.alumni_id)
    if alumni is not None:
        alumni.legal_name_verification_status = "rejected"

    request.status = "rejected"
    request.reviewed_by_user_id = current_user.id
    request.reviewed_at = datetime.now(timezone.utc)

    record_audit_log(
        db, user_id=current_user.id, action="reject", entity_type="legal_name_change_request",
        entity_id=request.id, details={"alumni_id": request.alumni_id},
    )
    db.commit()
    db.refresh(request)
    return request
