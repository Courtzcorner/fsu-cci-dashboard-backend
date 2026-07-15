from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, new_uuid


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    # Bcrypt hash only. Plaintext passwords are never stored or logged.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    organization_roles = relationship(
        "UserOrganizationRole", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User username={self.username!r}>"


class UserOrganizationRole(Base, TimestampMixin):
    """Maps a user to an organization with a specific role.

    This is the source of truth for access control: a user may only access
    organizations for which a row exists here.
    """

    __tablename__ = "user_organization_roles"
    __table_args__ = (UniqueConstraint("user_id", "organization_id", name="uq_user_organization"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)

    user = relationship("User", back_populates="organization_roles")
    organization = relationship("Organization", back_populates="user_roles")

    def __repr__(self) -> str:
        return f"<UserOrganizationRole user_id={self.user_id} org_id={self.organization_id} role={self.role}>"
