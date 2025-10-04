from functools import lru_cache
import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import logging


def _normalize_database_url(url: str | None) -> str | None:
    """Ensure postgres URLs use the psycopg v3 dialect."""

    if not url:
        return url

    if url.startswith("postgresql+psycopg://"):
        return url

    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)

    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)

    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)

    return url


def _default_database_url() -> str:
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        # Normalize common postgres schemes to SQLAlchemy-friendly forms
        return _normalize_database_url(explicit)

    mount_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if mount_path:
        return f"sqlite:///{Path(mount_path) / 'pricebot.db'}"

    return "sqlite:///./pricebot.db"


class Settings(BaseSettings):
    """Application configuration pulled from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        arbitrary_types_allowed=True,
    )

    app_name: str = "Pricebot API"
    environment: str = (
        os.getenv("ENVIRONMENT")
        or os.getenv("RAILWAY_ENVIRONMENT_NAME")
        or os.getenv("RAILWAY_ENVIRONMENT")
        or "local"
    )

    database_url: str = _default_database_url()
    alembic_database_url: str | None = None

    default_currency: str = "USD"
    ingestion_storage_dir: Path = Path(
        os.getenv("INGESTION_STORAGE_DIR")
        or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
        or "./storage"
    )

    enable_openai: bool = False
    openai_api_key: str | None = None

    # Pydantic v2: configuration is provided via `model_config` above

    @field_validator("database_url", "alembic_database_url", mode="before")
    @classmethod
    def _coerce_database_url(cls, value: str | None) -> str | None:
        """Ensure SQLAlchemy uses the psycopg v3 driver on postgres URLs."""

        return _normalize_database_url(value)


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings instance."""

    settings = Settings()
    try:
        settings.ingestion_storage_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - defensive
        logging.getLogger("pricebot.startup").exception(
            "Failed to ensure storage dir %s: %s", settings.ingestion_storage_dir, exc
        )
    return settings


settings = get_settings()
