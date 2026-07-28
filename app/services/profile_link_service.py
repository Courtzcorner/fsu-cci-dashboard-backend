"""
Lifecycle operations for linking a UserProfile to an Alumni record:
persisting computed candidates, user/admin confirmation, rejection,
unlinking, and conflict handling.

Nothing in this module ever writes to the `alumni` table - only to
`user_profiles` and `profile_match_candidates`. The imported Alumni
record is always read-only from this module's perspective.
"""
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.alumni import Alumni
from app.models.user_profile import LinkStatus, ProfileMatchCandidate, UserProfile
from app.services.identity_matching_service import compute_match_candidates


def find_and_persist_candidates(db: Session, profile: UserProfile) -> list[ProfileMatchCandidate]:
    """Recomputes qualifying candidates against the current active
    dataset and upserts them as ProfileMatchCandidate rows. Existing
    "confirmed" or "rejected" candidate rows are left untouched (they are
    a permanent record of a past decision) - only "candidate" rows are
    refreshed."""
    if profile.link_status in LinkStatus.CONFIRMED:
        # Already linked - re-running the matcher must never disturb an
        # existing confirmed link.
        return (
            db.query(ProfileMatchCandidate)
            .filter(ProfileMatchCandidate.user_profile_id == profile.id)
            .all()
        )

    computed = compute_match_candidates(db, profile)
    computed_by_alumni_id = {c.alumni.id: c for c in computed}

    existing_rows = {
        row.alumni_id: row
        for row in db.query(ProfileMatchCandidate).filter(ProfileMatchCandidate.user_profile_id == profile.id).all()
    }

    for alumni_id, candidate in computed_by_alumni_id.items():
        existing = existing_rows.get(alumni_id)
        if existing is not None and existing.status in ("confirmed", "rejected"):
            continue  # never resurrect a previously rejected/confirmed decision
        if existing is not None:
            existing.score = candidate.score
            existing.match_type = candidate.match_type
            existing.matched_signals = json.dumps(candidate.matched_signals)
        else:
            db.add(
                ProfileMatchCandidate(
                    user_profile_id=profile.id,
                    alumni_id=alumni_id,
                    match_type=candidate.match_type,
                    score=candidate.score,
                    matched_signals=json.dumps(candidate.matched_signals),
                    status="candidate",
                )
            )

    # A candidate row that no longer qualifies (e.g. the alumni record was
    # deactivated by a newer import, or the profile data changed) is
    # dropped only if it was never confirmed/rejected.
    for alumni_id, row in existing_rows.items():
        if alumni_id not in computed_by_alumni_id and row.status == "candidate":
            db.delete(row)

    if computed_by_alumni_id and profile.link_status == LinkStatus.UNMATCHED:
        profile.link_status = LinkStatus.CANDIDATE

    db.commit()
    return (
        db.query(ProfileMatchCandidate)
        .filter(ProfileMatchCandidate.user_profile_id == profile.id, ProfileMatchCandidate.status == "candidate")
        .order_by(ProfileMatchCandidate.score.desc())
        .all()
    )


def _other_confirmed_profile(db: Session, alumni_id: str, exclude_profile_id: str) -> Optional[UserProfile]:
    return (
        db.query(UserProfile)
        .filter(
            UserProfile.alumni_id == alumni_id,
            UserProfile.link_status.in_(LinkStatus.CONFIRMED),
            UserProfile.id != exclude_profile_id,
        )
        .first()
    )


def confirm_match(db: Session, profile: UserProfile, alumni_id: str) -> UserProfile:
    """The user says "this is me". Requires a persisted, still-qualifying
    candidate row for this exact (profile, alumni) pair - an endpoint
    caller can never confirm an alumni_id that the matcher never
    evaluated as a real candidate."""
    if profile.link_status in LinkStatus.CONFIRMED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Profile is already linked to an alumni record")

    candidate_row = (
        db.query(ProfileMatchCandidate)
        .filter(
            ProfileMatchCandidate.user_profile_id == profile.id,
            ProfileMatchCandidate.alumni_id == alumni_id,
            ProfileMatchCandidate.status == "candidate",
        )
        .first()
    )
    if candidate_row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="No qualifying match candidate found for this alumni record. Run POST /profile/me/find-match first.",
        )

    alumni = db.get(Alumni, alumni_id)
    if alumni is None or not alumni.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This alumni record is no longer part of the active dataset and cannot be linked.",
        )

    conflicting_profile = _other_confirmed_profile(db, alumni_id, profile.id)
    if conflicting_profile is not None:
        # Never silently steal a link from another confirmed account -
        # flag for admin review instead.
        profile.link_status = LinkStatus.CONFLICT
        profile.match_evidence = candidate_row.matched_signals
        candidate_row.status = "candidate"
        db.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Another account is already confirmed against this alumni record. Flagged for admin review.",
        )

    profile.alumni_id = alumni_id
    profile.link_status = LinkStatus.USER_CONFIRMED
    profile.link_confidence = candidate_row.score
    profile.linked_at = datetime.now(timezone.utc)
    profile.linked_by = "user"
    profile.match_evidence = candidate_row.matched_signals
    profile.needs_review = False
    candidate_row.status = "confirmed"
    db.commit()
    db.refresh(profile)
    return profile


def reject_match(db: Session, profile: UserProfile, alumni_id: str) -> None:
    candidate_row = (
        db.query(ProfileMatchCandidate)
        .filter(
            ProfileMatchCandidate.user_profile_id == profile.id,
            ProfileMatchCandidate.alumni_id == alumni_id,
        )
        .first()
    )
    if candidate_row is not None:
        candidate_row.status = "rejected"
    else:
        # No prior candidate row (e.g. rejecting a suggestion the caller
        # already knows about) - record the rejection anyway so it is
        # never resuggested.
        db.add(
            ProfileMatchCandidate(
                user_profile_id=profile.id,
                alumni_id=alumni_id,
                match_type="standard",
                score=0,
                matched_signals=json.dumps([]),
                status="rejected",
            )
        )

    remaining_candidates = (
        db.query(ProfileMatchCandidate)
        .filter(ProfileMatchCandidate.user_profile_id == profile.id, ProfileMatchCandidate.status == "candidate")
        .count()
    )
    if profile.link_status == LinkStatus.CANDIDATE and remaining_candidates == 0:
        profile.link_status = LinkStatus.UNMATCHED
    db.commit()


def unlink(db: Session, profile: UserProfile) -> None:
    """Clears the link on the UserProfile ONLY. The Alumni record and its
    imported data are never touched."""
    profile.alumni_id = None
    profile.link_status = LinkStatus.UNMATCHED
    profile.link_confidence = None
    profile.linked_at = None
    profile.linked_by = None
    profile.match_evidence = None
    profile.needs_review = False
    db.commit()


def sync_link_review_status(db: Session, profile: UserProfile) -> UserProfile:
    """Reactive (read-time) safety net for CSV reimport: if this
    profile's confirmed alumni record has since been deactivated by a
    newer replace-mode import, flag it for review - never silently
    unlink it or connect it to a different record. If the same alumni
    record becomes active again in a later import, the flag clears on
    its own the next time this is checked.

    This is intentionally decoupled from csv_import_service - it is
    invoked from the profile-reading endpoints below, never from the
    import pipeline itself.
    """
    if profile.link_status in LinkStatus.CONFIRMED and profile.alumni_id:
        alumni = db.get(Alumni, profile.alumni_id)
        should_review = alumni is None or not alumni.is_active
        if should_review != profile.needs_review:
            profile.needs_review = should_review
            db.commit()
            db.refresh(profile)
    return profile


def admin_approve(db: Session, profile: UserProfile, alumni_id: str, admin_user_id: str) -> UserProfile:
    alumni = db.get(Alumni, alumni_id)
    if alumni is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Alumni record not found")

    # An admin decision is authoritative: any other account previously
    # confirmed against this same alumni record is demoted to "conflict"
    # rather than left in an inconsistent double-confirmed state.
    other_confirmed = _other_confirmed_profile(db, alumni_id, profile.id)
    if other_confirmed is not None:
        other_confirmed.link_status = LinkStatus.CONFLICT
        other_confirmed.needs_review = True

    profile.alumni_id = alumni_id
    profile.link_status = LinkStatus.ADMIN_CONFIRMED
    profile.linked_at = datetime.now(timezone.utc)
    profile.linked_by = f"admin:{admin_user_id}"
    profile.needs_review = False

    candidate_row = (
        db.query(ProfileMatchCandidate)
        .filter(ProfileMatchCandidate.user_profile_id == profile.id, ProfileMatchCandidate.alumni_id == alumni_id)
        .first()
    )
    if candidate_row is not None:
        profile.link_confidence = candidate_row.score
        profile.match_evidence = candidate_row.matched_signals
        candidate_row.status = "confirmed"

    db.commit()
    db.refresh(profile)
    return profile


def admin_reject(db: Session, profile: UserProfile) -> UserProfile:
    profile.alumni_id = None
    profile.link_status = LinkStatus.REJECTED
    profile.link_confidence = None
    profile.linked_at = None
    profile.linked_by = None
    profile.needs_review = False
    db.commit()
    db.refresh(profile)
    return profile
