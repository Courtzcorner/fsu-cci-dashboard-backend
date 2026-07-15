from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class LocationAlias(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Controlled lookup table checked before general parsing rules.

    `alias` is stored lowercased/trimmed for case-insensitive matching.
    """

    __tablename__ = "location_aliases"

    alias: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    canonical_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    canonical_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    canonical_country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metro_area: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
