"""
Shared content endpoints: Events, Speakers, Super Stars.

All records live in the shared database (`events`, `speakers`,
`super_stars` tables) - there is no in-memory or frontend-only copy. GET
endpoints are public to any authenticated user and only ever return
`is_published=True` rows for the requested organization. Write endpoints
require the `admin` role and are durably persisted immediately.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, get_current_user, get_organization_by_slug_for_current_user, require_admin_role
from app.models.content import Event, Speaker, SuperStar
from app.models.organization import Organization
from app.schemas.content import (
    EventCreate,
    EventOut,
    EventUpdate,
    SpeakerCreate,
    SpeakerOut,
    SpeakerUpdate,
    SuperStarCreate,
    SuperStarOut,
    SuperStarUpdate,
)
from app.services.audit_service import record_audit_log
from app.services.content_version_service import (
    bump_for_event_change,
    bump_for_speaker_change,
    bump_for_superstar_change,
)

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
