from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.api.routes import documents, health, offers, price_history, products, vendors
from app.ui import views as operator_views
from app.core.config import settings
from app.db.session import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
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
