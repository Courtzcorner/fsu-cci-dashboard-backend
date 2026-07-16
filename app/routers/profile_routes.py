"""
Authenticated alumni self-service profile endpoints.

Alumni logs in -> JWT identifies the user -> user is linked to one alumni
record (users.alumni_id) -> GET /me/profile returns that record -> PATCH
allows editing only the whitelisted fields -> changes are written directly
to the shared `alumni` table.
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, get_current_user, require_alumni_profile
from app.models.alumni import Alumni
from app.models.legal_name import LegalNameChangeRequest
from app.schemas.profile import (
    LegalNameChangeRequestIn,
    LegalNameChangeRequestOut,
    ProfileOut,
    ProfilePhotoUploadResponse,
    ProfileUpdateRequest,
)
from app.services.audit_service import record_audit_log
from app.services.location_normalization_service import normalize_location
from app.services.storage_service import get_storage_service

router = APIRouter(prefix="/me", tags=["profile"])


def _to_profile_out(alumni: Alumni) -> ProfileOut:
    return ProfileOut(
        id=alumni.id,
        first_name=alumni.first_name,
        last_name=alumni.last_name,
        full_name=alumni.full_name,
        preferred_name=alumni.preferred_name,
        verified_legal_name=alumni.verified_legal_name,
        legal_name_verified=alumni.legal_name_verified,
        legal_name_verification_status=alumni.legal_name_verification_status,
        legal_name_verified_at=alumni.legal_name_verified_at,
        graduation_date=alumni.graduation_date,
        graduation_year=alumni.graduation_year,
        job_title=alumni.job_title,
        company=alumni.company,
        job_location=alumni.location_original,
        city=alumni.city,
        state=alumni.state,
        country=alumni.country,
        profile_image_url=alumni.profile_image_url,
        linkedin_url=alumni.linkedin_url,
        bio=alumni.bio,
        profile_visibility=alumni.profile_visibility,
        profile_completion=alumni.profile_completion,
    )


def _get_own_alumni(db: Session, current_user: CurrentUser) -> Alumni:
    alumni_id = require_alumni_profile(current_user)
    alumni = db.get(Alumni, alumni_id)
    if alumni is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alumni profile not found")
    return alumni


@router.get("/profile", response_model=ProfileOut)
def get_my_profile(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileOut:
    alumni = _get_own_alumni(db, current_user)
    return _to_profile_out(alumni)


@router.patch("/profile", response_model=ProfileOut)
def update_my_profile(
    payload: ProfileUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileOut:
    alumni = _get_own_alumni(db, current_user)

    updates = payload.model_dump(exclude_unset=True)

    job_location = updates.pop("job_location", None)
    if job_location is not None:
        alumni.location_original = job_location
        location_result = normalize_location(job_location, db=db)
        for field, value in location_result.as_dict().items():
            if field != "location_original":
                setattr(alumni, field, value)

    for field, value in updates.items():
        setattr(alumni, field, value)

    record_audit_log(
        db, user_id=current_user.id, action="update", entity_type="alumni_profile", entity_id=alumni.id,
        details={"fields": list(payload.model_dump(exclude_unset=True).keys())},
    )
    db.commit()
    db.refresh(alumni)
    return _to_profile_out(alumni)


@router.post("/profile/photo", response_model=ProfilePhotoUploadResponse)
async def upload_my_profile_photo(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfilePhotoUploadResponse:
    alumni = _get_own_alumni(db, current_user)

    contents = await file.read()
    storage = get_storage_service()
    url = storage.save_profile_photo(owner_id=alumni.id, file=file, contents=contents)

    alumni.profile_image_url = url
    record_audit_log(
        db, user_id=current_user.id, action="update", entity_type="alumni_profile_photo", entity_id=alumni.id,
    )
    db.commit()
    return ProfilePhotoUploadResponse(profile_image_url=url)


@router.post(
    "/legal-name-change-request",
    response_model=LegalNameChangeRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def request_legal_name_change(
    payload: LegalNameChangeRequestIn,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LegalNameChangeRequest:
    alumni = _get_own_alumni(db, current_user)

    request = LegalNameChangeRequest(
        alumni_id=alumni.id,
        requested_legal_name=payload.requested_legal_name,
        reason=payload.reason,
        status="pending_review",
    )
    db.add(request)
    alumni.legal_name_verification_status = "change_requested"
    record_audit_log(
        db, user_id=current_user.id, action="create", entity_type="legal_name_change_request",
        details={"alumni_id": alumni.id},
    )
    db.commit()
    db.refresh(request)
    return request
