"""
CSVImport and AuditLog: every admin write (CSV import, event/speaker/super
star create-update-delete, legal name review) is durably recorded in the
shared database, never only in memory or frontend state.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mixins import UUIDPrimaryKeyMixin, new_uuid, utcnow
from app.database import Base


class CSVImport(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "csv_imports"

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Raw row accounting from the uploaded file itself, persisted so GET
    # /admin/current-import can report the dataset's provenance long after
    # the original import response was returned.
    rows_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_valid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_invalid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    row_errors_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    organization_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
