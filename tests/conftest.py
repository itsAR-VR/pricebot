from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
import sys

import pytest
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.core.config import settings  # noqa: E402
from app.core.metrics import metrics  # noqa: E402
from app.services.whatsapp_scheduler import scheduler  # noqa: E402

from app.db import models  # noqa: F401, E402 - ensure models are imported for metadata


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def configure_whatsapp_settings():
    original_token = settings.whatsapp_ingest_token
    original_secret = settings.whatsapp_ingest_hmac_secret
    original_ttl = settings.whatsapp_ingest_signature_ttl_seconds
    original_rate = settings.whatsapp_ingest_rate_limit_per_minute
    original_burst = settings.whatsapp_ingest_rate_limit_burst
    original_debounce = scheduler.debounce_seconds

    settings.whatsapp_ingest_token = "test-token"
    settings.whatsapp_ingest_hmac_secret = None
    settings.whatsapp_ingest_signature_ttl_seconds = original_ttl
    settings.whatsapp_ingest_rate_limit_per_minute = original_rate
    settings.whatsapp_ingest_rate_limit_burst = original_burst
    scheduler.debounce_seconds = 60.0
    metrics._counters.clear()
    if hasattr(metrics, "_recent_failures"):
        metrics._recent_failures.clear()

    yield

    settings.whatsapp_ingest_token = original_token
    settings.whatsapp_ingest_hmac_secret = original_secret
    settings.whatsapp_ingest_signature_ttl_seconds = original_ttl
    settings.whatsapp_ingest_rate_limit_per_minute = original_rate
    settings.whatsapp_ingest_rate_limit_burst = original_burst
    scheduler.debounce_seconds = original_debounce
