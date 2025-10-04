from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.api.routes import documents, health, offers, price_history, products, vendors
from app.ui import views as operator_views
from app.core.config import settings
from app.db.session import init_db

logger = logging.getLogger("pricebot.startup")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("=== Pricebot Startup ===")
    logger.info("PORT: %s", os.getenv("PORT", "NOT SET"))
    logger.info("ENVIRONMENT: %s", settings.environment)
    logger.info("DATABASE_URL: %s", "***" if settings.database_url else "NOT SET")
    logger.info("INGESTION_STORAGE_DIR: %s", settings.ingestion_storage_dir)
    logger.info("========================")
    
    # Avoid crashing the app during startup if the database is unavailable.
    # Healthchecks should succeed even if DB is temporarily down.
    try:
        init_db()
        logger.info("Database initialization completed successfully")
    except Exception as exc:  # pragma: no cover - defensive startup
        logger.exception("Database initialization skipped due to error: %s", exc)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.include_router(health.router)
app.include_router(offers.router)
app.include_router(products.router)
app.include_router(vendors.router)
app.include_router(price_history.router)
app.include_router(documents.router)
app.include_router(operator_views.router)
app.include_router(operator_views.upload_router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/upload", status_code=307)


@app.get("/metadata", summary="Service metadata")
def service_metadata() -> dict[str, str]:
    return {"service": settings.app_name, "environment": settings.environment}
