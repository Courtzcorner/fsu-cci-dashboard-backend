"""
Centralized application configuration.

All sensitive/environment-specific values are read from environment
variables (optionally loaded from a local .env file via python-dotenv).
Nothing sensitive should ever be hard-coded here.
"""
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Database ---
    database_url: str = "sqlite:///./dev.db"

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
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

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
    """Fail fast if production is misconfigured with insecure defaults."""
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
        raise RuntimeError("DATABASE_URL must point at PostgreSQL in production, not SQLite.")
