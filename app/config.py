"""
Centralized application configuration.

All sensitive/environment-specific values are read from environment
variables (optionally loaded from a local .env file via python-dotenv).
Nothing sensitive should ever be hard-coded here.
"""
import os
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

# Render sets these on every service automatically - used to force
# production-grade validation (real Postgres, strong secret, explicit
# CORS origins) even if ENVIRONMENT was left unset/misconfigured on the
# dashboard. This is what previously allowed the app to silently boot
# against an ephemeral local SQLite file on Render and lose all imported
# data on every restart/redeploy/scale-to-zero.
_RENDER_ENV_MARKERS = ("RENDER", "RENDER_SERVICE_ID", "RENDER_INSTANCE_ID")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Database ---
    # No default on purpose: DATABASE_URL must always be explicitly set
    # (via .env locally, or a real Render env var in production). If it's
    # missing, the app must fail to start rather than silently falling
    # back to a local SQLite file that disappears on every restart.
    database_url: str

    # --- File storage (profile photo uploads) ---
    # "local" stores files under UPLOADS_DIR and serves them from /uploads.
    # Swap in "s3" (or another provider) later by implementing it in
    # app/services/storage_service.py behind the same interface - the app
    # must keep working with "local" and no external credentials.
    storage_provider: str = "local"
    uploads_dir: str = "uploads"
    public_base_url: str = "http://localhost:8000"
    max_upload_size_mb: int = 5

    # --- Security / JWT ---
    jwt_secret_key: str = "insecure-dev-secret-key-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # --- CORS ---
    allowed_origins: str = ""

    # --- Multi-organization ---
    default_organization_slug: str = "fsu-cci"

    # --- App / environment ---
    environment: str = "development"
    enable_api_docs: bool = True

    # --- Rate limiting ---
    login_rate_limit: str = "5/minute"

    # --- Optional geocoding (disabled by default; see location_normalization_service) ---
    geocoding_enabled: bool = False
    geocoding_provider_api_key: str = ""

    @property
    def is_render(self) -> bool:
        return any(os.environ.get(marker) for marker in _RENDER_ENV_MARKERS)

    @property
    def is_production(self) -> bool:
        # Treated as production if explicitly configured OR if we detect
        # we're running on Render at all - a Render deployment should
        # never be allowed to quietly behave like a dev sandbox.
        return self.environment.lower() in {"production", "prod"} or self.is_render

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def uploads_dir_full_path(self) -> Path:
        path = Path(self.uploads_dir)
        return path if path.is_absolute() else BASE_DIR / path



@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_settings_for_production(settings: Settings) -> None:
    """Fail fast if production (including any detected Render deployment)
    is misconfigured with insecure or non-persistent defaults."""
    if not settings.is_production:
        return

    if settings.jwt_secret_key == "insecure-dev-secret-key-change-me" or len(settings.jwt_secret_key) < 32:
        raise RuntimeError(
            "JWT_SECRET_KEY must be set to a strong, unique value in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
        )

    if not settings.cors_origins_list:
        raise RuntimeError(
            "ALLOWED_ORIGINS must be set to your frontend's origin(s) in production."
        )

    if settings.is_sqlite:
        raise RuntimeError(
            "DATABASE_URL must point at PostgreSQL in production, not SQLite. "
            "On Render this almost always means the DATABASE_URL environment "
            "variable was never set on the service (or was set on the wrong "
            "service) - imported data will be lost on every restart/redeploy "
            "until this is fixed. Set DATABASE_URL to your Render Postgres "
            "instance's 'Internal Database URL' (or 'External Database URL' "
            "if connecting from outside Render)."
        )
