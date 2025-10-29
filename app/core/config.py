from functools import lru_cache
import os
from pathlib import Path
from typing import Optional, List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import logging


def _normalize_database_url(url: Optional[str]) -> Optional[str]:
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
    alembic_database_url: Optional[str] = None

    default_currency: str = "USD"
    ingestion_storage_dir: Path = Path(
        os.getenv("INGESTION_STORAGE_DIR")
        or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
        or "./storage"
    )
    log_buffer_size: int = 500
    log_tool_event_size: int = 200
    log_buffer_file: Optional[Path] = None

    enable_openai: bool = False
    openai_api_key: Optional[str] = None
    # Use LLM to assist product resolution/ranking in chat tools
    use_llm_product_resolve: bool = True

    # Ingest settings
    whatsapp_ingest_token: Optional[str] = None
    whatsapp_ingest_hmac_secret: Optional[str] = None
    whatsapp_ingest_signature_ttl_seconds: int = 300
    whatsapp_content_hash_window_hours: int = 24
    whatsapp_ingest_rate_limit_per_minute: int = 600
    whatsapp_ingest_rate_limit_burst: int = 200
    whatsapp_extract_debounce_seconds: float = 10.0
    whatsapp_media_storage_backend: str = "local"
    whatsapp_media_s3_bucket: Optional[str] = None
    whatsapp_media_s3_prefix: str = "whatsapp-media/"
    whatsapp_media_s3_region: Optional[str] = None
    whatsapp_media_s3_endpoint_url: Optional[str] = None
    whatsapp_media_gcs_bucket: Optional[str] = None
    whatsapp_media_gcs_prefix: str = "whatsapp-media/"
    whatsapp_media_max_bytes: int = 15 * 1024 * 1024
    whatsapp_media_storage_timeout_seconds: float = 20.0

    # CORS settings (kept simple for now; default open for local/dev)
    cors_allow_all: bool = True
    cors_allowed_origins: List[str] = []

    # Pydantic v2: configuration is provided via `model_config` above

    @field_validator("database_url", "alembic_database_url", mode="before")
    @classmethod
    def _coerce_database_url(cls, value: Optional[str]) -> Optional[str]:
        """Ensure SQLAlchemy uses the psycopg v3 driver on postgres URLs."""

        return _normalize_database_url(value)

    @field_validator("log_buffer_file", mode="before")
    @classmethod
    def _coerce_log_buffer_file(cls, value: Optional[str]) -> Optional[Path]:
        if value in (None, "", "None"):
            return None
        return Path(value)

    @field_validator("whatsapp_media_storage_backend", mode="before")
    @classmethod
    def _normalize_media_backend(cls, value: Optional[str]) -> str:
        normalized = (value or "local").strip().lower()
        if normalized not in {"local", "s3", "gcs"}:
            raise ValueError("whatsapp_media_storage_backend must be 'local', 's3', or 'gcs'")
        return normalized

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
