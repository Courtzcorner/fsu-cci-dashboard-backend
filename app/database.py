"""
SQLAlchemy 2.0 engine/session setup.

DATABASE_URL drives the dialect. Production must use PostgreSQL; SQLite is
supported for local dev/tests only (see config.validate_settings_for_production).
"""
import logging
from typing import Generator
from urllib.parse import urlsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.is_sqlite else {}

engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True)

# Log (never the credentials) which database this process is actually
# talking to, so a misconfigured DATABASE_URL is obvious in the Render
# logs instead of silently causing "data disappears after restart".
_parsed_db_url = urlsplit(settings.database_url)
logger.info(
    "Database engine initialized: dialect=%s host=%s db=%s",
    _parsed_db_url.scheme,
    _parsed_db_url.hostname or "(local file)",
    _parsed_db_url.path.lstrip("/") or "(unknown)",
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
