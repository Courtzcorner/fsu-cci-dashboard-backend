import csv
import io
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser, get_current_user, require_admin_role
from app.models.alumni import Alumni, AlumniOrganization
from app.models.audit import CSVImport
from app.models.legal_name import LegalNameChangeRequest
from app.models.organization import Organization
from app.models.user import User
from app.models.user_profile import LinkStatus, ProfileMatchCandidate, UserProfile
from app.schemas.admin import CurrentImportOut, ImportResult, NormalizeLocationsResult, RowError, UserAdminOut
from app.schemas.profile import LegalNameChangeRequestOut
from app.schemas.user_profile import AdminProfileLinkActionOut, AdminProfileMatchCandidateOut, MatchCandidateOut
from app.services.audit_service import record_audit_log
from app.services.csv_import_service import IMPORT_LOGIC_VERSION, import_alumni_csv
from app.services.location_reprocess_service import reprocess_locations
from app.services.profile_link_service import admin_approve, admin_reject, sync_link_review_status

EXPORT_COLUMNS = [
    "First Name", "Last Name", "Email", "LinkedIn URL", "Company Name", "Job Title",
    "City", "State", "Education", "Verification Status", "Verification Date",
    "Industry", "Industry Source", "Career Category", "Career Category Source",
    "Seniority", "Seniority Source",
    # Effective-data layer: original imported value, user-supplied
    # profile override (only when a CONFIRMED link exists), and the
    # resulting effective (displayed) value - never destroying the
    # imported source columns above.
    "Imported Company", "Profile Company", "Effective Company",
    "Imported Job Title", "Profile Job Title", "Effective Job Title",
    "Imported University", "Profile University", "Effective University",
    "Imported City", "Profile City", "Effective City",
    "Profile Link Status", "Profile Updated At",
]
# Batch size used for the keyset-paginated export query below - keeps
# memory bounded (~batch_size rows in memory at a time) no matter how
# large the active dataset is (75,000+ rows).
EXPORT_BATCH_SIZE = 1000

router = APIRouter(prefix="/admin", tags=["admin"])


def _resolve_organization(db: Session, slug: str) -> Organization:
    organization = db.query(Organization).filter(Organization.slug == slug).first()
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return organization


def _get_default_organization(db: Session) -> Organization:
    """The product exposes a single dashboard/dataset: the admin never
    selects or submits an organization. Internally this still resolves to
    the configured default organization slug (kept for architectural
    compatibility), but that value is never taken from client input.
    """
    return _resolve_organization(db, get_settings().default_organization_slug)


@router.get("/users", response_model=list[UserAdminOut])
def list_users(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[UserAdminOut]:
    """Every login account's current state, including its up-to-date
    username after a first-login credential setup - never a password or
    password hash."""
    require_admin_role(current_user)
    users = db.query(User).order_by(User.username.asc()).all()
    return [
        UserAdminOut(
            id=user.id,
            username=user.username,
            role=user.role,
            is_active=user.is_active,
            alumni_id=user.alumni_id,
            must_change_credentials=user.must_change_credentials,
            temporary_account_created_at=user.temporary_account_created_at,
            credentials_updated_at=user.credentials_updated_at,
            previous_username=user.previous_username,
            username_changed_at=user.username_changed_at,
        )
        for user in users
    ]


@router.post("/import-alumni", response_model=ImportResult)
async def import_alumni(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportResult:
    """Live CSV import path (replace-v2):

    router: app.routers.admin_routes.import_alumni
      -> service: app.services.csv_import_service.import_alumni_csv
      -> transaction: single db.commit() after create/update + deactivate
      -> final count: Alumni.is_active == True for this organization

    There is no legacy merge/upsert import function wired to this route:
    every successful import replaces the organization's active dataset.
    """
    # There is only one dashboard/dataset: the admin does not - and cannot -
    # choose an organization. This always imports into (and replaces) the
    # backend's single configured default organization's dataset.
    require_admin_role(current_user)
    organization_record = _get_default_organization(db)

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .csv files are accepted")

    contents = await file.read()

    try:
        summary = import_alumni_csv(
            db, organization_record, contents, imported_by_user_id=current_user.id, filename=file.filename
        )
    except Exception:
        # import_alumni_csv already rolled back its own transaction on
        # failure; nothing was committed, so the previous active dataset
        # remains fully intact. Never report success for a failed import.
        logger.exception(
            "CSV import failed for organization=%s; transaction rolled back, previous active dataset preserved",
            organization_record.slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Import failed and was rolled back. The previously active dataset is unchanged.",
        )

    return ImportResult(
        organization=organization_record.slug,
        filename=summary.filename,
        created=summary.created,
        updated=summary.updated,
        unchanged=summary.unchanged,
        skipped=summary.skipped,
        failed=summary.failed,
        deactivated=summary.archived,
        archived=summary.archived,
        # Deprecated: kept only for backward compatibility. Always equals
        # active_database_total - never the cumulative historical count.
        database_total=summary.active_database_total,
        active_database_total=summary.active_database_total,
        historical_database_total=summary.historical_database_total,
        row_errors=[RowError(**e) for e in summary.row_errors],
        csv_import_id=summary.csv_import_id,
        status=summary.status,
        import_logic_version=summary.import_logic_version,
        csv_physical_lines=summary.csv_physical_lines,
        csv_header_rows=summary.csv_header_rows,
        csv_data_rows=summary.csv_data_rows,
        csv_valid_rows=summary.csv_valid_rows,
        csv_invalid_rows=summary.csv_invalid_rows,
        csv_duplicate_rows=summary.csv_duplicate_rows,
        csv_rows_received=summary.csv_rows_received,
        csv_rows_valid=summary.csv_rows_valid,
        csv_rows_invalid=summary.csv_rows_invalid,
        # Canonical short names - always reflect this actual upload
        # (never hardcoded).
        rows_received=summary.csv_rows_received,
        rows_valid=summary.csv_rows_valid,
        rows_invalid=summary.csv_rows_invalid,
        duplicate_rows=summary.csv_duplicate_rows,
        recognized_headers=summary.recognized_headers,
        unrecognized_headers=summary.unrecognized_headers,
        rows_with_graduation_year=summary.rows_with_graduation_year,
        rows_with_major=summary.rows_with_major,
        rows_with_university=summary.rows_with_university,
        rows_with_job_title=summary.rows_with_job_title,
        rows_with_company=summary.rows_with_company,
        rows_with_location=summary.rows_with_location,
        rows_with_city=summary.rows_with_city,
        rows_with_state=summary.rows_with_state,
        rows_with_raw_city=summary.rows_with_raw_city,
        rows_with_raw_state=summary.rows_with_raw_state,
        rows_with_constructed_location=summary.rows_with_constructed_location,
        first_row_original=summary.first_row_original,
        first_row_normalized=summary.first_row_normalized,
        selected_company_column=summary.selected_company_column,
        selected_location_column=summary.selected_location_column,
        selected_city_column=summary.selected_city_column,
        selected_state_column=summary.selected_state_column,
        selected_university_column=summary.selected_university_column,
        selected_degree_column=summary.selected_degree_column,
        selected_major_column=summary.selected_major_column,
        selected_graduation_year_column=summary.selected_graduation_year_column,
        newly_created_identifiers=summary.newly_created_identifiers,
        duplicate_candidates_found=summary.duplicate_candidates_found,
    )


@router.get("/current-import", response_model=CurrentImportOut)
def get_current_import(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentImportOut:
    """Metadata for the single dataset currently powering the dashboard -
    the latest successfully committed CSV import. Only successful imports
    ever write a CSVImport row, so "most recent by created_at" is always
    "most recent successful" - a failed import never appears here and
    never changes what this endpoint reports. Any authenticated user
    (admin or alumni) may check this, mirroring read access to
    /alumni-data.
    """
    organization_record = _get_default_organization(db)

    active_total = (
        db.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization_record.id, Alumni.is_active.is_(True))
        .count()
    )

    latest_import = (
        db.query(CSVImport)
        .filter(CSVImport.organization_id == organization_record.id)
        .order_by(CSVImport.created_at.desc())
        .first()
    )
    if latest_import is None:
        return CurrentImportOut(
            import_logic_version=IMPORT_LOGIC_VERSION,
            active_database_total=active_total,
            status="none",
        )

    imported_at = latest_import.created_at.isoformat() if latest_import.created_at else None
    return CurrentImportOut(
        import_logic_version=IMPORT_LOGIC_VERSION,
        csv_import_id=latest_import.id,
        filename=latest_import.filename,
        uploaded_at=imported_at,
        imported_at=imported_at,
        csv_data_rows=latest_import.rows_received,
        csv_rows_received=latest_import.rows_received,
        rows_received=latest_import.rows_received,
        rows_valid=latest_import.rows_valid,
        rows_invalid=latest_import.rows_invalid,
        active_database_total=active_total,
        status="complete",
    )


def _active_alumni_export_query(db: Session, organization_id: str):
    return (
        db.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization_id, Alumni.is_active.is_(True))
    )


def _profile_override(profile: UserProfile | None, show_flag: str, profile_field: str) -> str | None:
    """Same privacy-respecting override rule used everywhere else (see
    app.services.effective_alumni_service): only a nonblank profile
    value, on a CONFIRMED link, with the owner's visibility flag on."""
    if profile is None:
        return None
    if not getattr(profile, show_flag, False):
        return None
    value = getattr(profile, profile_field, None)
    return value if value else None


def _export_row(alumni: Alumni, profile: UserProfile | None) -> list:
    profile_company = _profile_override(profile, "show_current_employer", "current_employer")
    profile_job_title = _profile_override(profile, "show_job_title", "current_job_title")
    profile_university = _profile_override(profile, "show_education", "current_university")
    profile_city = _profile_override(profile, "show_location", "current_city")

    return [
        alumni.first_name,
        alumni.last_name,
        alumni.email,
        alumni.linkedin_url,
        alumni.company,
        alumni.job_title,
        alumni.city,
        alumni.state,
        alumni.university,
        alumni.verification_status,
        alumni.verification_date.isoformat() if alumni.verification_date else "",
        alumni.industry,
        alumni.industry_source,
        alumni.career_category,
        alumni.career_category_source,
        alumni.seniority,
        alumni.seniority_source,
        # Effective-data layer (never overwrites the imported columns above).
        alumni.company,
        profile_company,
        profile_company or alumni.company,
        alumni.job_title,
        profile_job_title,
        profile_job_title or alumni.job_title,
        alumni.university,
        profile_university,
        profile_university or alumni.university,
        alumni.city,
        profile_city,
        profile_city or alumni.city,
        profile.link_status if profile else LinkStatus.UNMATCHED,
        profile.updated_at.isoformat() if profile and profile.updated_at else "",
    ]


@router.get("/export-alumni")
def export_alumni(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Streams the current active dataset (imported CSV columns plus
    every backend-derived column and its provenance) as a CSV download.
    Never overwrites imported source values - `industry`/`career_category`/
    `seniority` here are exactly what's stored on each Alumni row, and each
    has its own `*_source` column (imported / derived:title_rules /
    company_mapping / unknown) alongside it.

    Rows are fetched in keyset-paginated batches (ordered by id, not
    OFFSET) so a 75,000-row export never materializes the full dataset in
    memory at once.
    """
    require_admin_role(current_user)
    organization_record = _get_default_organization(db)
    base_query = _active_alumni_export_query(db, organization_record.id)

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(EXPORT_COLUMNS)
        yield buffer.getvalue()

        last_id: str | None = None
        while True:
            buffer.seek(0)
            buffer.truncate(0)
            query = base_query.order_by(Alumni.id.asc())
            if last_id is not None:
                query = query.filter(Alumni.id > last_id)
            batch = query.limit(EXPORT_BATCH_SIZE).all()
            if not batch:
                break

            # One extra query per batch (not per row) for confirmed
            # profile overrides - keeps this an O(rows / batch_size)
            # query pattern even at 75,000+ rows, never N+1.
            batch_ids = [alumni.id for alumni in batch]
            confirmed_profiles = {
                row.alumni_id: row
                for row in db.query(UserProfile)
                .filter(UserProfile.alumni_id.in_(batch_ids), UserProfile.link_status.in_(LinkStatus.CONFIRMED))
                .all()
            }

            for alumni in batch:
                writer.writerow(_export_row(alumni, confirmed_profiles.get(alumni.id)))
                last_id = alumni.id
            yield buffer.getvalue()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=alumni_export.csv"},
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


# --------------------------------------------------------------------------
# Alumni profile <-> directory link review (additive; never touches the
# CSV import pipeline, Alumni records, or existing analytics).
# --------------------------------------------------------------------------


def _profile_needs_admin_attention(db: Session, profile: UserProfile) -> bool:
    sync_link_review_status(db, profile)
    return profile.link_status in (LinkStatus.CANDIDATE, LinkStatus.CONFLICT) or profile.needs_review


@router.get("/profile-match-candidates", response_model=list[AdminProfileMatchCandidateOut])
def list_profile_match_candidates(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AdminProfileMatchCandidateOut]:
    """Every user profile that needs admin attention: multiple qualifying
    candidates, a rejected-by-conflict link, or a confirmed link whose
    alumni record was deactivated by a newer CSV import."""
    require_admin_role(current_user)

    profiles = (
        db.query(UserProfile)
        .filter(UserProfile.link_status.in_([LinkStatus.CANDIDATE, LinkStatus.CONFLICT]))
        .all()
    )
    # Also surface confirmed links flagged needs_review by a later import.
    confirmed_profiles = (
        db.query(UserProfile)
        .filter(UserProfile.link_status.in_(LinkStatus.CONFIRMED))
        .all()
    )
    for profile in confirmed_profiles:
        sync_link_review_status(db, profile)
    review_flagged = [p for p in confirmed_profiles if p.needs_review]

    results: list[AdminProfileMatchCandidateOut] = []
    for profile in [*profiles, *review_flagged]:
        user = db.get(User, profile.user_id)
        candidate_rows = (
            db.query(ProfileMatchCandidate)
            .filter(ProfileMatchCandidate.user_profile_id == profile.id, ProfileMatchCandidate.status == "candidate")
            .order_by(ProfileMatchCandidate.score.desc())
            .all()
        )
        candidates = []
        for row in candidate_rows:
            alumni = db.get(Alumni, row.alumni_id)
            if alumni is None:
                continue
            candidates.append(
                MatchCandidateOut(
                    alumni_id=alumni.id,
                    full_name=alumni.full_name,
                    university=alumni.university,
                    company=alumni.company,
                    job_title=alumni.job_title,
                    city=alumni.city,
                    state=alumni.state,
                    match_type=row.match_type,
                    score=row.score,
                    matched_signals=json.loads(row.matched_signals),
                )
            )
        full_name = None
        if profile.first_name or profile.last_name:
            full_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip()
        results.append(
            AdminProfileMatchCandidateOut(
                user_profile_id=profile.id,
                user_id=profile.user_id,
                username=user.username if user else "",
                profile_full_name=full_name,
                link_status=profile.link_status,
                needs_review=profile.needs_review,
                candidates=candidates,
            )
        )
    return results


def _get_profile_or_404(db: Session, profile_id: str) -> UserProfile:
    profile = db.get(UserProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User profile not found")
    return profile


def _to_admin_link_action_out(profile: UserProfile) -> AdminProfileLinkActionOut:
    return AdminProfileLinkActionOut(
        user_profile_id=profile.id,
        alumni_id=profile.alumni_id,
        link_status=profile.link_status,
        linked_at=profile.linked_at,
        linked_by=profile.linked_by,
        needs_review=profile.needs_review,
    )


@router.post("/profile-links/{profile_id}/approve", response_model=AdminProfileLinkActionOut)
def approve_profile_link(
    profile_id: str,
    alumni_id: str = Form(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdminProfileLinkActionOut:
    """Admin confirms `profile_id` is linked to `alumni_id`. If another
    account was previously confirmed against the same alumni record, it
    is demoted to `conflict` (never silently left double-confirmed)."""
    require_admin_role(current_user)
    profile = _get_profile_or_404(db, profile_id)
    alumni = db.get(Alumni, alumni_id)
    if alumni is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alumni record not found")
    record_audit_log(
        db, user_id=current_user.id, action="approve", entity_type="profile_link",
        entity_id=profile.id, details={"alumni_id": alumni_id},
    )
    profile = admin_approve(db, profile, alumni_id, current_user.id)
    return _to_admin_link_action_out(profile)


@router.post("/profile-links/{profile_id}/reject", response_model=AdminProfileLinkActionOut)
def reject_profile_link(
    profile_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdminProfileLinkActionOut:
    require_admin_role(current_user)
    profile = _get_profile_or_404(db, profile_id)
    record_audit_log(
        db, user_id=current_user.id, action="reject", entity_type="profile_link", entity_id=profile.id,
    )
    profile = admin_reject(db, profile)
    return _to_admin_link_action_out(profile)


@router.delete("/profile-links/{profile_id}", response_model=AdminProfileLinkActionOut)
def unlink_profile_link(
    profile_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdminProfileLinkActionOut:
    """Admin-initiated unlink. Neither the UserProfile row nor the Alumni
    record is deleted - only the link between them is cleared."""
    require_admin_role(current_user)
    profile = _get_profile_or_404(db, profile_id)
    record_audit_log(
        db, user_id=current_user.id, action="unlink", entity_type="profile_link", entity_id=profile.id,
    )
    from app.services.profile_link_service import unlink as unlink_profile

    unlink_profile(db, profile)
    db.refresh(profile)
    return _to_admin_link_action_out(profile)
