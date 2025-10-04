from functools import lru_cache
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_database_url() -> str:
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit

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


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings instance."""

    settings = Settings()
    settings.ingestion_storage_dir.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
