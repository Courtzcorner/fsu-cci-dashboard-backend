"""
Normalized reference tables for companies, industries, and universities.

Populated opportunistically during CSV import (get-or-create per
organization) so the `/companies`, `/industries`, and `/universities`
frontend pages have a stable, deduplicated source to page through, in
addition to the free-text values already stored per-alumni.
"""
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Company(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_company_org_name"),)

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class Industry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "industries"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_industry_org_name"),)

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class University(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "universities"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_university_org_name"),)

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
