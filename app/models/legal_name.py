from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import UUIDPrimaryKeyMixin, utcnow


class LegalNameChangeRequest(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "legal_name_change_requests"

    alumni_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("alumni.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_legal_name: Mapped[str] = mapped_column(String(256), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_review", nullable=False)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
