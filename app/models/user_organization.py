from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class UserOrganization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Explicit per-organization membership for a login account (admin or
    alumni). Additive alongside `users.role`, which remains the account's
    global/legacy role and is never modified by this table.

    `role` is nullable: null means "inherit users.role" for this
    organization. A user with zero rows here is a "legacy" account not
    yet migrated to explicit per-organization access - see
    app.deps.get_authorized_organization for exactly how that fallback
    behaves. This is Phase 1 infrastructure only: nothing yet creates
    UserOrganization rows (no backfill has run), and no existing endpoint
    depends on this table.
    """

    __tablename__ = "user_organizations"
    __table_args__ = (UniqueConstraint("user_id", "organization_id", name="uq_user_organization"),)

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Not DB-enum-constrained, matching the existing users.role convention.
    # Must always be validated against app.models.roles.resolve_effective_role
    # before being used for authorization or returned through any API.
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)

    user = relationship("User", back_populates="organization_links")
    organization = relationship("Organization", back_populates="user_links")

    def __repr__(self) -> str:
        return (
            f"<UserOrganization user_id={self.user_id!r} "
            f"organization_id={self.organization_id!r} role={self.role!r}>"
        )
