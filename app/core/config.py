from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration pulled from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        arbitrary_types_allowed=True,
    )

    app_name: str = "Pricebot API"
    environment: str = "local"

    database_url: str = "sqlite:///./pricebot.db"
    alembic_database_url: str | None = None

    default_currency: str = "USD"
    ingestion_storage_dir: Path = Path("./storage")

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
