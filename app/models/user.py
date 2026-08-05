from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A login account. Exactly one global role (`admin` or `alumni`),
    preserved for the lifetime of the account - never changed by the
    first-login credential setup flow below.

    `alumni_id` links an alumni's login account to their one alumni
    record (nullable - admin accounts typically have no alumni record).
    """

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    # Bcrypt hash only. Plaintext passwords are never stored or logged.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="alumni")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    alumni_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("alumni.id", ondelete="SET NULL"), unique=True, nullable=True
    )

    # --- First-login (temporary account) credential setup - additive ---
    # True only for the small set of seeded temporary accounts until they
    # complete POST /auth/complete-first-login. Every pre-existing/normal
    # account defaults to False and is never forced through this flow.
    must_change_credentials: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    temporary_account_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    credentials_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Audit trail for the one-time username change performed during
    # credential setup - the User row (id, role, alumni_id, and every
    # relationship keyed off user_id) is never recreated, only updated.
    previous_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    username_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Minimal additive JWT claim ("tv"): bumping this invalidates every
    # previously issued token for this user (used by /auth/logout and by
    # credential setup to revoke the temporary session), without a
    # server-side token blacklist/store. Tokens issued before this column
    # existed carry no "tv" claim and are treated as tv=0, matching the
    # column's default - so no pre-existing session is broken by this
    # change.
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    alumni = relationship("Alumni", back_populates="user_account")
    # Phase 1 infrastructure only (see app.models.user_organization) - no
    # rows exist yet, and no existing endpoint reads this relationship.
    organization_links = relationship(
        "UserOrganization", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User username={self.username!r} role={self.role!r}>"
