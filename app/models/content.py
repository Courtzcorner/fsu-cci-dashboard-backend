"""
Shared, organization-scoped content managed by admins and viewed by
alumni: Events, Speakers, and Super Stars. All records live in the same
database used for alumni/auth data - there are no separate admin/alumni
copies. Only `is_published=True` records are ever returned from the
public GET endpoints.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Event(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "events"

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(4000), nullable=True)

    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    virtual_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    registration_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    organization = relationship("Organization")


class Speaker(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "speakers"

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bio: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    profile_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    speaking_topics: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    availability_status: Mapped[str | None] = mapped_column(String(64), nullable=True)

    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    organization = relationship("Organization")


class SuperStar(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "super_stars"

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alumni_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("alumni.id", ondelete="CASCADE"), nullable=False, index=True
    )
    headline: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    featured_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    featured_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    organization = relationship("Organization")
    alumni = relationship("Alumni")
