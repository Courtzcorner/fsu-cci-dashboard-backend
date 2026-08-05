from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # "national" | "institution" - used only to shape available-context
    # presentation/filtering (see app.services.organization_context_service).
    # Never used as the sole authorization check on its own.
    context_type: Mapped[str] = mapped_column(String(32), nullable=False, default="institution")
    # Frontend theming hint only - nullable, never required for backend logic.
    theme_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    alumni_links = relationship("AlumniOrganization", back_populates="organization", cascade="all, delete-orphan")
    user_links = relationship("UserOrganization", back_populates="organization", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Organization slug={self.slug!r}>"
