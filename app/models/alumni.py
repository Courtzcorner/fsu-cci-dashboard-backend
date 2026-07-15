from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, new_uuid


class Alumni(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "alumni"

    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[str] = mapped_column(String(128), nullable=False)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)

    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    major: Mapped[str | None] = mapped_column(String(255), nullable=True)
    degree: Mapped[str | None] = mapped_column(String(255), nullable=True)
    university: Mapped[str | None] = mapped_column(String(255), nullable=True)

    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    career_category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    seniority: Mapped[str | None] = mapped_column(String(64), nullable=True)

    industry_source: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    career_category_source: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    seniority_source: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)

    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)

    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), default="unverified", nullable=False)
    verification_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    profile_completion: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Location ---
    # location_original is the exact imported spreadsheet value. Never
    # overwrite or discard it; all normalization writes only to the fields
    # below.
    location_original: Mapped[str | None] = mapped_column(String(512), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    state: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    state_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metro_area: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    display_location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_normalization_status: Mapped[str] = mapped_column(String(32), default="missing", nullable=False)
    location_normalized_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    organization_links = relationship(
        "AlumniOrganization", back_populates="alumni", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Alumni full_name={self.full_name!r}>"


class AlumniOrganization(Base, TimestampMixin):
    """Many-to-many link between alumni and organizations.

    An alumni record may belong to more than one organization (e.g. a
    fsu-cci graduate who is also part of stars-national). Duplicate
    detection during import is always scoped per-organization.
    """

    __tablename__ = "alumni_organizations"
    __table_args__ = (UniqueConstraint("alumni_id", "organization_id", name="uq_alumni_organization"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    alumni_id: Mapped[str] = mapped_column(String(36), ForeignKey("alumni.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    alumni = relationship("Alumni", back_populates="organization_links")
    organization = relationship("Organization", back_populates="alumni_links")
