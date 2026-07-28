"""
Public (any-authenticated-user) alumni profile page: combines safe
imported directory fields (already visible via GET /alumni-data) with
privacy-gated, user-supplied profile data - never both an imported and
a user-supplied field at once, and never a private field the owner has
not explicitly opted to share.

"Public" here means "visible to any authenticated dashboard user",
consistent with the rest of this API - there is no unauthenticated
access anywhere in this backend, and this endpoint does not change that.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, get_current_user
from app.models.alumni import Alumni
from app.models.user_profile import LinkStatus, UserProfile
from app.schemas.user_profile import PublicEducationHistoryOut, PublicProfileOut, PublicWorkHistoryOut
from app.services.profile_link_service import sync_link_review_status

router = APIRouter(tags=["public-profiles"])


@router.get("/public-profiles/{alumni_id}", response_model=PublicProfileOut)
def get_public_profile(
    alumni_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PublicProfileOut:
    alumni = db.get(Alumni, alumni_id)
    if alumni is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Alumni record not found")

    profile = (
        db.query(UserProfile)
        .filter(UserProfile.alumni_id == alumni_id, UserProfile.link_status.in_(LinkStatus.CONFIRMED))
        .first()
    )

    out = PublicProfileOut(
        alumni_id=alumni.id,
        full_name=alumni.full_name,
        has_user_profile=profile is not None,
        university=alumni.university,
        company=alumni.company,
        job_title=alumni.job_title,
        city=alumni.city,
        state=alumni.state,
        display_location=alumni.display_location,
        verification_status=alumni.verification_status,
    )

    if profile is None:
        return out

    profile = sync_link_review_status(db, profile)

    out.profile_photo_url = profile.profile_photo_url
    out.preferred_name = profile.preferred_name
    out.headline = profile.headline
    out.bio = profile.bio
    out.pronouns = profile.pronouns
    out.hometown = profile.hometown
    out.available_to_speak = profile.available_to_speak
    out.available_to_mentor = profile.available_to_mentor
    out.speaker_topics = profile.speaker_topics
    out.mentoring_topics = profile.mentoring_topics

    # --- Server-side privacy enforcement: every private field below is
    # populated ONLY if the corresponding show_* flag is True. The
    # frontend never receives a private value it has to hide itself. ---
    if profile.show_email:
        out.email = profile.primary_email
    if profile.show_phone:
        out.phone_number = profile.phone_number
    if profile.show_birthday:
        out.birthday = profile.birthday
    if profile.show_location:
        # current_city/current_state are additive to the already-public
        # imported city/state - no imported field is hidden either way.
        pass
    if profile.show_current_employer:
        pass  # alumni.company (imported) is already public in the directory
    if profile.show_job_title:
        pass  # alumni.job_title (imported) is already public in the directory
    if profile.show_linkedin:
        out.linkedin_url = profile.linkedin_url
    if profile.show_social_links:
        out.github_url = profile.github_url
        out.instagram_url = profile.instagram_url
        out.x_url = profile.x_url
        out.personal_website_url = profile.personal_website_url
    if profile.show_work_history:
        out.work_history = [
            PublicWorkHistoryOut(
                employer=w.employer,
                job_title=w.job_title,
                start_date=w.start_date,
                end_date=w.end_date,
                is_current=w.is_current,
                description=w.description,
            )
            for w in profile.work_history
        ]
    if profile.show_education_history or profile.show_education:
        out.education_history = [
            PublicEducationHistoryOut(
                institution=e.institution,
                degree=e.degree,
                field_of_study=e.field_of_study,
                start_year=e.start_year,
                graduation_year=e.graduation_year,
                is_current=e.is_current,
            )
            for e in profile.education_history
        ]

    return out
