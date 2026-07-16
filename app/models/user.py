from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A login account. Exactly one global role (`admin` or `alumni`).

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

    alumni = relationship("Alumni", back_populates="user_account")

    def __repr__(self) -> str:
        return f"<User username={self.username!r} role={self.role!r}>"
