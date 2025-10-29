import logging
import os
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.log_buffer import install_log_buffer, record_tool_call
from app.db.session import init_db
from app.api.routes import (
    chat_stream,
    chat_tools,
    documents,
    health,
    metrics,
    offers,
    price_history,
    products,
    vendors,
)
from app.api.routes import integrations_whatsapp
from app.ui import views as operator_views

logger = logging.getLogger("pricebot.startup")

install_log_buffer(
    max_logs=settings.log_buffer_size,
    max_tool_events=settings.log_tool_event_size,
    file_path=settings.log_buffer_file,
)


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

# CORS: allow-all in dev; restrict in prod unless explicitly configured
env_lower = settings.environment.lower()
if settings.cors_allow_all and env_lower not in {"prod", "production"}:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"]
    )
elif settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"]
    )

app.include_router(health.router)
app.include_router(metrics.router)
app.include_router(offers.router)
app.include_router(products.router)
app.include_router(vendors.router)
app.include_router(price_history.router)
app.include_router(documents.router)
app.include_router(chat_tools.router)
app.include_router(chat_stream.router)
app.include_router(operator_views.router)
app.include_router(operator_views.upload_router)
app.include_router(operator_views.chat_router)
app.include_router(operator_views.whatsapp_router)
app.include_router(integrations_whatsapp.router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/upload", status_code=307)


@app.get("/metadata", summary="Service metadata")
def service_metadata() -> dict[str, str]:
    return {"service": settings.app_name, "environment": settings.environment}


@app.middleware("http")
async def capture_chat_tool_requests(request: Request, call_next):
    start = perf_counter()
    status_code: int = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        if request.url.path.startswith("/chat/tools"):
            duration_ms = (perf_counter() - start) * 1000.0
            record_tool_call(
                method=request.method,
                path=request.url.path,
                status=status_code,
                duration_ms=duration_ms,
                conversation_id=request.headers.get("x-conversation-id"),
            )
