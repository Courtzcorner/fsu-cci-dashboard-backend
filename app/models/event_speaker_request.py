"""
An alumnus's request to speak at a specific published Event, and the
admin decision on it. Distinct from `Speaker` (app.models.content),
which is an admin-curated, free-text speaker directory not linked to
any Alumni record or Event.

Status is a plain string (mirroring `LegalNameChangeRequest.status`)
with exactly two supported values: "requested" (the default, set on
submission) and "selected" (set by an admin, and reversible back to
"requested" - there is no separate "rejected" state for this feature;
an admin simply leaves a request as "requested" or moves it back to
that state).
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class EventSpeakerRequestStatus:
    REQUESTED = "requested"
    SELECTED = "selected"


class EventSpeakerRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "event_speaker_requests"
    __table_args__ = (
        UniqueConstraint("event_id", "alumni_id", name="uq_speaker_request_event_alumni"),
    )

    # Denormalized from the event at creation time (never from the
    # requesting alumnus) so every admin query can filter directly on
    # organization_id in one step, exactly like Event/Speaker/SuperStar
    # already do - the event itself remains the sole source of truth,
    # enforced in the router by requiring event.organization_id ==
    # the resolved organization at both submission and admin-read time.
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alumni_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("alumni.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default=EventSpeakerRequestStatus.REQUESTED, nullable=False
    )
    selected_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization")
    event = relationship("Event")
    alumni = relationship("Alumni")
