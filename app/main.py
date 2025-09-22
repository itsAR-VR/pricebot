from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health, offers
from app.core.config import settings
from app.db.session import init_db


@asynccontextmanager
def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.include_router(health.router)
app.include_router(offers.router)


@app.get("/", summary="Service metadata")
def root() -> dict[str, str]:
    return {"service": settings.app_name, "environment": settings.environment}
