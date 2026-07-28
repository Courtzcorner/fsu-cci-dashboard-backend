"""
Self-service alumni profile + identity-matching endpoints.

Attaches entirely to the CURRENTLY AUTHENTICATED USER (via
`get_current_user` / `CurrentUser.id`) - it never creates, modifies, or
depends on how that user logged in. Authentication, JWTs, roles, and
CORS are untouched by this module.

This is fully additive: it never writes to the `alumni` table (imported
CSV data) and never touches the CSV import pipeline.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, get_current_user
from app.models.alumni import Alumni
from app.models.user_profile import (
    LinkStatus,
    ProfileMatchCandidate,
    UserEducationHistory,
    UserProfile,
    UserWorkHistory,
)
from app.schemas.user_profile import (
    EducationHistoryIn,
    EducationHistoryOut,
    FindMatchResponse,
    LinkActionResponse,
    MatchCandidateOut,
    PrivacySettingsIn,
    PrivacySettingsOut,
    ProfileEnvelopeOut,
    UserProfileOut,
    UserProfileUpdateRequest,
    WorkHistoryIn,
    WorkHistoryOut,
)
from app.services.effective_profile_service import recompute_profile_effective_fields
from app.services.identity_matching_service import matched_field_names, nonmatching_field_names
from app.services.profile_link_service import (
    confirm_match,
    find_and_persist_candidates,
    reject_match,
    sync_link_review_status,
    unlink,
)

router = APIRouter(prefix="/profile", tags=["user-profile"])


def _get_or_create_profile(db: Session, current_user: CurrentUser) -> UserProfile:
    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if profile is None:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return sync_link_review_status(db, profile)


def _to_privacy_out(profile: UserProfile) -> PrivacySettingsOut:
    return PrivacySettingsOut.model_validate(profile)


def _to_profile_out(profile: UserProfile) -> UserProfileOut:
    return UserProfileOut(
        id=profile.id,
        user_id=profile.user_id,
        profile_photo_url=profile.profile_photo_url,
        first_name=profile.first_name,
        last_name=profile.last_name,
        preferred_name=profile.preferred_name,
        headline=profile.headline,
        current_city=profile.current_city,
        current_state=profile.current_state,
        current_country=profile.current_country,
        current_job_title=profile.current_job_title,
        current_employer=profile.current_employer,
        current_university=profile.current_university,
        degree=profile.degree,
        field_of_study=profile.field_of_study,
        graduation_year=profile.graduation_year,
        bio=profile.bio,
        current_industry=profile.current_industry,
        effective_industry_source=profile.effective_industry_source,
        email=profile.primary_email,
        primary_email=profile.primary_email,
        secondary_email=profile.secondary_email,
        phone_number=profile.phone_number,
        personal_website=profile.personal_website,
        birthday=profile.birthday,
        pronouns=profile.pronouns,
        hometown=profile.hometown,
        linkedin_url=profile.linkedin_url,
        github_url=profile.github_url,
        instagram_url=profile.instagram_url,
        x_url=profile.x_url,
        personal_website_url=profile.personal_website_url,
        available_to_speak=profile.available_to_speak,
        available_to_mentor=profile.available_to_mentor,
        speaker_topics=profile.speaker_topics,
        mentoring_topics=profile.mentoring_topics,
        preferred_engagement_types=profile.preferred_engagement_types,
        privacy=_to_privacy_out(profile),
        alumni_id=profile.alumni_id,
        link_status=profile.link_status,
        link_confidence=profile.link_confidence,
        linked_at=profile.linked_at,
        linked_by=profile.linked_by,
        needs_review=profile.needs_review,
        work_history=[WorkHistoryOut.model_validate(w) for w in profile.work_history],
        education_history=[EducationHistoryOut.model_validate(e) for e in profile.education_history],
    )


def _to_envelope(profile: UserProfile) -> ProfileEnvelopeOut:
    return ProfileEnvelopeOut(profile=_to_profile_out(profile), is_linked=profile.link_status in LinkStatus.CONFIRMED)


def _candidate_rows_to_out(db: Session, rows: list[ProfileMatchCandidate]) -> list[MatchCandidateOut]:
    out = []
    for row in rows:
        alumni = db.get(Alumni, row.alumni_id)
        if alumni is None:
            continue
        matched_signals = json.loads(row.matched_signals)
        out.append(
            MatchCandidateOut(
                alumni_id=alumni.id,
                full_name=alumni.full_name,
                name=alumni.full_name,
                university=alumni.university,
                company=alumni.company,
                job_title=alumni.job_title,
                city=alumni.city,
                state=alumni.state,
                match_type=row.match_type,
                score=row.score,
                match_score=row.score,
                matched_signals=matched_signals,
                matched_fields=matched_field_names(matched_signals),
                nonmatching_fields=nonmatching_field_names(matched_signals),
                confirmation_required=True,
            )
        )
    return out


# --------------------------------------------------------------------------
# Profile CRUD
# --------------------------------------------------------------------------


@router.get("/me", response_model=ProfileEnvelopeOut)
def get_my_profile(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileEnvelopeOut:
    """Always returns an editable profile, whether or not this account has
    ever been linked to an alumni directory record - a directory link is
    entirely optional. This endpoint auto-creates an empty UserProfile
    row on first access; it never 404s just because `alumni_id` is null."""
    profile = _get_or_create_profile(db, current_user)
    return _to_envelope(profile)


@router.put("/me", response_model=ProfileEnvelopeOut)
def update_my_profile(
    payload: UserProfileUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileEnvelopeOut:
    """Saves profile fields regardless of link status - linking to an
    alumni record is never a prerequisite for filling out or editing a
    profile."""
    profile = _get_or_create_profile(db, current_user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    recompute_profile_effective_fields(db, profile)
    db.commit()
    db.refresh(profile)
    return _to_envelope(profile)


@router.get("/me/privacy", response_model=PrivacySettingsOut)
def get_my_privacy_settings(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PrivacySettingsOut:
    profile = _get_or_create_profile(db, current_user)
    return _to_privacy_out(profile)


@router.put("/me/privacy", response_model=PrivacySettingsOut)
def update_my_privacy_settings(
    payload: PrivacySettingsIn,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PrivacySettingsOut:
    profile = _get_or_create_profile(db, current_user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return _to_privacy_out(profile)


# --------------------------------------------------------------------------
# Work history
# --------------------------------------------------------------------------


@router.get("/me/work-history", response_model=list[WorkHistoryOut])
def list_my_work_history(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[UserWorkHistory]:
    profile = _get_or_create_profile(db, current_user)
    return profile.work_history


@router.post("/me/work-history", response_model=WorkHistoryOut, status_code=status.HTTP_201_CREATED)
def create_my_work_history_entry(
    payload: WorkHistoryIn,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserWorkHistory:
    profile = _get_or_create_profile(db, current_user)
    entry = UserWorkHistory(user_profile_id=profile.id, **payload.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def _get_own_work_history_entry(db: Session, profile: UserProfile, entry_id: str) -> UserWorkHistory:
    entry = db.get(UserWorkHistory, entry_id)
    if entry is None or entry.user_profile_id != profile.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Work history entry not found")
    return entry


@router.put("/me/work-history/{entry_id}", response_model=WorkHistoryOut)
def update_my_work_history_entry(
    entry_id: str,
    payload: WorkHistoryIn,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserWorkHistory:
    profile = _get_or_create_profile(db, current_user)
    entry = _get_own_work_history_entry(db, profile, entry_id)
    for field, value in payload.model_dump().items():
        setattr(entry, field, value)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/me/work-history/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_work_history_entry(
    entry_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    profile = _get_or_create_profile(db, current_user)
    entry = _get_own_work_history_entry(db, profile, entry_id)
    db.delete(entry)
    db.commit()


# --------------------------------------------------------------------------
# Education history
# --------------------------------------------------------------------------


@router.get("/me/education-history", response_model=list[EducationHistoryOut])
def list_my_education_history(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[UserEducationHistory]:
    profile = _get_or_create_profile(db, current_user)
    return profile.education_history


@router.post("/me/education-history", response_model=EducationHistoryOut, status_code=status.HTTP_201_CREATED)
def create_my_education_history_entry(
    payload: EducationHistoryIn,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserEducationHistory:
    profile = _get_or_create_profile(db, current_user)
    entry = UserEducationHistory(user_profile_id=profile.id, **payload.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def _get_own_education_history_entry(db: Session, profile: UserProfile, entry_id: str) -> UserEducationHistory:
    entry = db.get(UserEducationHistory, entry_id)
    if entry is None or entry.user_profile_id != profile.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Education history entry not found")
    return entry


@router.put("/me/education-history/{entry_id}", response_model=EducationHistoryOut)
def update_my_education_history_entry(
    entry_id: str,
    payload: EducationHistoryIn,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserEducationHistory:
    profile = _get_or_create_profile(db, current_user)
    entry = _get_own_education_history_entry(db, profile, entry_id)
    for field, value in payload.model_dump().items():
        setattr(entry, field, value)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/me/education-history/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_education_history_entry(
    entry_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    profile = _get_or_create_profile(db, current_user)
    entry = _get_own_education_history_entry(db, profile, entry_id)
    db.delete(entry)
    db.commit()


# --------------------------------------------------------------------------
# Identity matching / alumni record linking
# --------------------------------------------------------------------------


@router.post("/me/find-match", response_model=FindMatchResponse)
def find_my_match_candidates(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FindMatchResponse:
    """Deterministic matching only - see
    app.services.identity_matching_service for the full policy. Never
    auto-links; only ever returns candidates that already qualify for
    user confirmation."""
    profile = _get_or_create_profile(db, current_user)
    candidate_rows = find_and_persist_candidates(db, profile)
    return FindMatchResponse(
        link_status=profile.link_status,
        candidates=_candidate_rows_to_out(db, candidate_rows),
    )


@router.get("/me/match-candidates", response_model=FindMatchResponse)
def get_my_match_candidates(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FindMatchResponse:
    profile = _get_or_create_profile(db, current_user)
    rows = (
        db.query(ProfileMatchCandidate)
        .filter(ProfileMatchCandidate.user_profile_id == profile.id, ProfileMatchCandidate.status == "candidate")
        .order_by(ProfileMatchCandidate.score.desc())
        .all()
    )
    return FindMatchResponse(link_status=profile.link_status, candidates=_candidate_rows_to_out(db, rows))


@router.post("/me/confirm-match/{alumni_id}", response_model=LinkActionResponse)
def confirm_my_match(
    alumni_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LinkActionResponse:
    profile = _get_or_create_profile(db, current_user)
    profile = confirm_match(db, profile, alumni_id)
    return LinkActionResponse(
        link_status=profile.link_status,
        alumni_id=profile.alumni_id,
        link_confidence=profile.link_confidence,
        linked_at=profile.linked_at,
        linked_by=profile.linked_by,
        needs_review=profile.needs_review,
    )


@router.post("/me/reject-match/{alumni_id}", status_code=status.HTTP_204_NO_CONTENT)
def reject_my_match(
    alumni_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    profile = _get_or_create_profile(db, current_user)
    reject_match(db, profile, alumni_id)


@router.delete("/me/link", response_model=LinkActionResponse)
def delete_my_link(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LinkActionResponse:
    """Unlinks this profile from its alumni record. Neither the Alumni
    record nor any other user's data is ever modified or deleted."""
    profile = _get_or_create_profile(db, current_user)
    unlink(db, profile)
    return LinkActionResponse(link_status=profile.link_status, needs_review=False)
