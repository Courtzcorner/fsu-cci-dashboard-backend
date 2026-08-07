"""
Shared content endpoints: Events, Speakers, Super Stars.

All records live in the shared database (`events`, `speakers`,
`super_stars` tables) - there is no in-memory or frontend-only copy. GET
endpoints are public to any authenticated user and only ever return
`is_published=True` rows for the requested organization. Write endpoints
require the `admin` role and are durably persisted immediately.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import (
    CurrentUser,
    get_current_user,
    get_organization_by_slug_for_current_user,
    require_admin_role,
    require_alumni_profile,
    require_alumni_role,
)
from app.models.alumni import Alumni
from app.models.content import Event, Speaker, SuperStar
from app.models.event_speaker_request import EventSpeakerRequest, EventSpeakerRequestStatus
from app.models.organization import Organization
from app.schemas.content import (
    EventCreate,
    EventOut,
    EventUpdate,
    SpeakerCreate,
    SpeakerOut,
    SpeakerRequestCreate,
    SpeakerRequestOut,
    SpeakerUpdate,
    SuperStarCreate,
    SuperStarOut,
    SuperStarUpdate,
)
from app.schemas.user_profile import PublicProfileOut
from app.services.audit_service import record_audit_log
from app.services.content_version_service import (
    bump_for_event_change,
    bump_for_speaker_change,
    bump_for_superstar_change,
)
from app.routers.public_profile_routes import build_public_profile

router = APIRouter(tags=["content"])


def _get_or_404(db: Session, model, entity_id: str, organization_id: str):
    record = (
        db.query(model)
        .filter(model.id == entity_id, model.organization_id == organization_id)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{model.__name__} not found")
    return record


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


@router.get("/events", response_model=list[EventOut])
def list_events(
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Event]:
    return (
        db.query(Event)
        .filter(Event.organization_id == organization.id, Event.is_published.is_(True))
        .order_by(Event.start_date.asc())
        .all()
    )


@router.post("/admin/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(
    payload: EventCreate,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Event:
    require_admin_role(current_user)
    event = Event(organization_id=organization.id, created_by_user_id=current_user.id, **payload.model_dump())
    db.add(event)
    db.flush()
    record_audit_log(
        db, user_id=current_user.id, action="create", entity_type="event", entity_id=event.id,
        organization_id=organization.id,
    )
    bump_for_event_change(db, change_type="event_create", updated_by_user_id=current_user.id, resource_id=event.id)
    db.commit()
    return event


@router.patch("/admin/events/{event_id}", response_model=EventOut)
def update_event(
    event_id: str,
    payload: EventUpdate,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Event:
    require_admin_role(current_user)
    event = _get_or_404(db, Event, event_id, organization.id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    record_audit_log(
        db, user_id=current_user.id, action="update", entity_type="event", entity_id=event.id,
        organization_id=organization.id,
    )
    bump_for_event_change(db, change_type="event_update", updated_by_user_id=current_user.id, resource_id=event.id)
    db.commit()
    return event


@router.delete("/admin/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(
    event_id: str,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    require_admin_role(current_user)
    event = _get_or_404(db, Event, event_id, organization.id)
    record_audit_log(
        db, user_id=current_user.id, action="delete", entity_type="event", entity_id=event.id,
        organization_id=organization.id,
    )
    deleted_event_id = event.id
    bump_for_event_change(db, change_type="event_delete", updated_by_user_id=current_user.id, resource_id=deleted_event_id)
    db.delete(event)
    db.commit()


# --------------------------------------------------------------------------
# Event speaker requests
#
# Distinct from the `Speaker` admin-curated directory below: this is an
# alumnus-submitted request to speak at a specific Event, reviewable and
# selectable by an admin. Every query is scoped to BOTH
# EventSpeakerRequest.organization_id == organization.id AND event_id ==
# the path's event_id, so a wrong-organization or wrong-event request_id
# is always a plain 404 - never a 200 with another organization's data,
# and never a distinguishable "exists but you can't see it" response.
# --------------------------------------------------------------------------


def _get_event_or_404(db: Session, event_id: str, organization_id: str) -> Event:
    return _get_or_404(db, Event, event_id, organization_id)


def _get_speaker_request_or_404(
    db: Session, request_id: str, event_id: str, organization_id: str
) -> EventSpeakerRequest:
    request = (
        db.query(EventSpeakerRequest)
        .filter(
            EventSpeakerRequest.id == request_id,
            EventSpeakerRequest.event_id == event_id,
            EventSpeakerRequest.organization_id == organization_id,
        )
        .first()
    )
    if request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Speaker request not found")
    return request


def _to_speaker_request_out(request: EventSpeakerRequest, alumni: Alumni) -> SpeakerRequestOut:
    return SpeakerRequestOut(
        id=request.id,
        organization_id=request.organization_id,
        event_id=request.event_id,
        alumni_id=request.alumni_id,
        alumni_full_name=alumni.full_name,
        alumni_job_title=alumni.job_title,
        alumni_company=alumni.company,
        message=request.message,
        status=request.status,
        selected_by_user_id=request.selected_by_user_id,
        selected_at=request.selected_at,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


@router.post(
    "/events/{event_id}/speaker-requests",
    response_model=SpeakerRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def request_to_speak(
    event_id: str,
    payload: SpeakerRequestCreate,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SpeakerRequestOut:
    # Alumni-only (unlike every other endpoint in this file, which follows
    # the generic "any authenticated user" content precedent): an admin
    # account is never allowed to submit a speaker request just because
    # it's authenticated, even if it happens to have a linked alumni_id.
    # Admin's only path to influence a request is the select/unselect
    # endpoints below.
    require_alumni_role(current_user)
    # Always the CALLER's own linked alumni record - `payload` has no
    # alumni_id field at all, so there is no way for a client to supply
    # or override it. Reuses the exact same account-to-alumni link (and
    # 404-if-unlinked error) as every other self-service endpoint (see
    # app.routers.profile_routes._get_own_alumni /
    # app.routers.profile_routes.request_legal_name_change) - no second
    # linking system.
    alumni_id = require_alumni_profile(current_user)
    alumni = db.get(Alumni, alumni_id)
    if alumni is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alumni profile not found")

    event = _get_event_or_404(db, event_id, organization.id)
    if not event.is_published:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    existing = (
        db.query(EventSpeakerRequest)
        .filter(EventSpeakerRequest.event_id == event.id, EventSpeakerRequest.alumni_id == alumni.id)
        .first()
    )
    if existing is not None:
        return _to_speaker_request_out(existing, alumni)

    request = EventSpeakerRequest(
        organization_id=organization.id,
        event_id=event.id,
        alumni_id=alumni.id,
        message=payload.message,
        status=EventSpeakerRequestStatus.REQUESTED,
    )
    db.add(request)
    db.flush()
    record_audit_log(
        db, user_id=current_user.id, action="create", entity_type="event_speaker_request", entity_id=request.id,
        organization_id=organization.id,
    )
    db.commit()
    db.refresh(request)
    return _to_speaker_request_out(request, alumni)


@router.get("/admin/events/{event_id}/speaker-requests", response_model=list[SpeakerRequestOut])
def list_speaker_requests(
    event_id: str,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SpeakerRequestOut]:
    require_admin_role(current_user)
    _get_event_or_404(db, event_id, organization.id)

    rows = (
        db.query(EventSpeakerRequest, Alumni)
        .join(Alumni, Alumni.id == EventSpeakerRequest.alumni_id)
        .filter(
            EventSpeakerRequest.event_id == event_id,
            EventSpeakerRequest.organization_id == organization.id,
        )
        .order_by(EventSpeakerRequest.created_at.asc())
        .all()
    )
    return [_to_speaker_request_out(request, alumni) for request, alumni in rows]


@router.get(
    "/admin/events/{event_id}/speaker-requests/{request_id}/alumni-profile",
    response_model=PublicProfileOut,
)
def get_speaker_request_alumni_profile(
    event_id: str,
    request_id: str,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PublicProfileOut:
    require_admin_role(current_user)
    _get_event_or_404(db, event_id, organization.id)
    request = _get_speaker_request_or_404(db, request_id, event_id, organization.id)
    # Reuses the exact same privacy-gated logic as GET /public-profiles/{id}
    # (see app.routers.public_profile_routes) - never a separate privacy
    # model, and never more than the alumnus has already made public.
    return build_public_profile(db, request.alumni_id)


@router.post(
    "/admin/events/{event_id}/speaker-requests/{request_id}/select",
    response_model=SpeakerRequestOut,
)
def select_speaker_request(
    event_id: str,
    request_id: str,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SpeakerRequestOut:
    require_admin_role(current_user)
    _get_event_or_404(db, event_id, organization.id)
    request = _get_speaker_request_or_404(db, request_id, event_id, organization.id)

    request.status = EventSpeakerRequestStatus.SELECTED
    request.selected_by_user_id = current_user.id
    request.selected_at = datetime.now(timezone.utc)
    record_audit_log(
        db, user_id=current_user.id, action="select", entity_type="event_speaker_request", entity_id=request.id,
        organization_id=organization.id,
    )
    db.commit()
    db.refresh(request)
    alumni = db.get(Alumni, request.alumni_id)
    return _to_speaker_request_out(request, alumni)


@router.post(
    "/admin/events/{event_id}/speaker-requests/{request_id}/unselect",
    response_model=SpeakerRequestOut,
)
def unselect_speaker_request(
    event_id: str,
    request_id: str,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SpeakerRequestOut:
    require_admin_role(current_user)
    _get_event_or_404(db, event_id, organization.id)
    request = _get_speaker_request_or_404(db, request_id, event_id, organization.id)

    request.status = EventSpeakerRequestStatus.REQUESTED
    request.selected_by_user_id = None
    request.selected_at = None
    record_audit_log(
        db, user_id=current_user.id, action="unselect", entity_type="event_speaker_request", entity_id=request.id,
        organization_id=organization.id,
    )
    db.commit()
    db.refresh(request)
    alumni = db.get(Alumni, request.alumni_id)
    return _to_speaker_request_out(request, alumni)


# --------------------------------------------------------------------------
# Speakers
# --------------------------------------------------------------------------


@router.get("/speakers", response_model=list[SpeakerOut])
def list_speakers(
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Speaker]:
    return (
        db.query(Speaker)
        .filter(Speaker.organization_id == organization.id, Speaker.is_published.is_(True))
        .order_by(Speaker.name.asc())
        .all()
    )


@router.post("/admin/speakers", response_model=SpeakerOut, status_code=status.HTTP_201_CREATED)
def create_speaker(
    payload: SpeakerCreate,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Speaker:
    require_admin_role(current_user)
    speaker = Speaker(organization_id=organization.id, created_by_user_id=current_user.id, **payload.model_dump())
    db.add(speaker)
    db.flush()
    record_audit_log(
        db, user_id=current_user.id, action="create", entity_type="speaker", entity_id=speaker.id,
        organization_id=organization.id,
    )
    bump_for_speaker_change(
        db, change_type="speaker_create", updated_by_user_id=current_user.id, resource_id=speaker.id
    )
    db.commit()
    return speaker


@router.patch("/admin/speakers/{speaker_id}", response_model=SpeakerOut)
def update_speaker(
    speaker_id: str,
    payload: SpeakerUpdate,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Speaker:
    require_admin_role(current_user)
    speaker = _get_or_404(db, Speaker, speaker_id, organization.id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(speaker, field, value)
    record_audit_log(
        db, user_id=current_user.id, action="update", entity_type="speaker", entity_id=speaker.id,
        organization_id=organization.id,
    )
    bump_for_speaker_change(
        db, change_type="speaker_update", updated_by_user_id=current_user.id, resource_id=speaker.id
    )
    db.commit()
    return speaker


@router.delete("/admin/speakers/{speaker_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_speaker(
    speaker_id: str,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    require_admin_role(current_user)
    speaker = _get_or_404(db, Speaker, speaker_id, organization.id)
    record_audit_log(
        db, user_id=current_user.id, action="delete", entity_type="speaker", entity_id=speaker.id,
        organization_id=organization.id,
    )
    deleted_speaker_id = speaker.id
    bump_for_speaker_change(
        db, change_type="speaker_delete", updated_by_user_id=current_user.id, resource_id=deleted_speaker_id
    )
    db.delete(speaker)
    db.commit()


# --------------------------------------------------------------------------
# Super Stars
# --------------------------------------------------------------------------


@router.get("/super-stars", response_model=list[SuperStarOut])
def list_super_stars(
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SuperStar]:
    return (
        db.query(SuperStar)
        .filter(SuperStar.organization_id == organization.id, SuperStar.is_published.is_(True))
        .order_by(SuperStar.featured_at.desc())
        .all()
    )


@router.post("/admin/super-stars", response_model=SuperStarOut, status_code=status.HTTP_201_CREATED)
def create_super_star(
    payload: SuperStarCreate,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SuperStar:
    require_admin_role(current_user)

    from app.models.alumni import Alumni

    alumni = db.get(Alumni, payload.alumni_id)
    if alumni is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alumni record not found")

    super_star = SuperStar(organization_id=organization.id, created_by_user_id=current_user.id, **payload.model_dump())
    db.add(super_star)
    db.flush()
    record_audit_log(
        db, user_id=current_user.id, action="create", entity_type="super_star", entity_id=super_star.id,
        organization_id=organization.id,
    )
    bump_for_superstar_change(
        db, change_type="superstar_create", updated_by_user_id=current_user.id, resource_id=super_star.id
    )
    db.commit()
    return super_star


@router.patch("/admin/super-stars/{super_star_id}", response_model=SuperStarOut)
def update_super_star(
    super_star_id: str,
    payload: SuperStarUpdate,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SuperStar:
    require_admin_role(current_user)
    super_star = _get_or_404(db, SuperStar, super_star_id, organization.id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(super_star, field, value)
    record_audit_log(
        db, user_id=current_user.id, action="update", entity_type="super_star", entity_id=super_star.id,
        organization_id=organization.id,
    )
    # Covers every mutation to an existing Super STAR - including
    # feature/unfeature, which is just an `is_published`/`featured_at`
    # change through this same endpoint.
    bump_for_superstar_change(
        db, change_type="superstar_update", updated_by_user_id=current_user.id, resource_id=super_star.id
    )
    db.commit()
    return super_star


@router.delete("/admin/super-stars/{super_star_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_super_star(
    super_star_id: str,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    require_admin_role(current_user)
    super_star = _get_or_404(db, SuperStar, super_star_id, organization.id)
    record_audit_log(
        db, user_id=current_user.id, action="delete", entity_type="super_star", entity_id=super_star.id,
        organization_id=organization.id,
    )
    deleted_super_star_id = super_star.id
    bump_for_superstar_change(
        db, change_type="superstar_delete", updated_by_user_id=current_user.id, resource_id=deleted_super_star_id
    )
    db.delete(super_star)
    db.commit()
