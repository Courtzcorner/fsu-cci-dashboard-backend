"""
Shared content-synchronization version tracking: a small, cheap-to-read
table that lets every logged-in client detect "something admin-managed
changed" without recalculating analytics or loading alumni rows.

Exactly one row per domain (including the special "global" domain) is
kept and updated IN PLACE on every successful admin mutation - this is
deliberately NOT an append-only event log, so GET /sync/status only ever
reads a handful of rows regardless of how many mutations have ever
happened. See app.services.content_version_service for the read/write
helpers, and app.routers.sync_routes for the endpoint.

Nothing here stores a user-specific copy of shared data - only an
integer counter, a timestamp, and who/what/which-resource triggered the
most recent bump for that domain (for observability, not for computing
current state).
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import UUIDPrimaryKeyMixin, utcnow

# Every domain a client can subscribe to via GET /sync/status, plus the
# special "global" domain (bumped alongside every other domain bump).
# Keep this in sync with app.services.content_version_service.ALL_DOMAINS.
CONTENT_DOMAINS: tuple[str, ...] = (
    "alumni", "analytics", "locations", "events", "superstars", "speakers", "universities", "profiles",
)
GLOBAL_DOMAIN = "global"


class ContentVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "content_versions"
    __table_args__ = (UniqueConstraint("domain", name="uq_content_versions_domain"),)

    domain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    updated_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # A short machine-readable label for the kind of change that most
    # recently bumped this domain (e.g. "csv_import", "event_create",
    # "profile_link_confirmed") - purely observability, never read by the
    # sync-status logic itself.
    change_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The id of the specific resource (CSVImport, Event, Speaker,
    # SuperStar, UserProfile, ...) whose change most recently bumped this
    # domain - not a foreign key, since it can point at rows in several
    # different tables depending on `domain`/`change_type`.
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    def __repr__(self) -> str:
        return f"<ContentVersion domain={self.domain!r} version={self.version}>"
