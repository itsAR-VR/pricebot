from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import select

from app.api.deps import get_db
from app.core.config import settings
from app.db import models
from app.main import app


def _override_get_db(session):
    def _get_db():
        yield session
    return _get_db


def test_upload_endpoint_persists_document(monkeypatch, tmp_path, session):
    app.dependency_overrides[get_db] = _override_get_db(session)
    monkeypatch.setattr(settings, "ingestion_storage_dir", tmp_path)

    client = TestClient(app)
    file_content = "Product,Price\nWidget,9.99\n"
    response = client.post(
        "/documents/upload",
        files={"file": ("sample.csv", file_content, "text/csv")},
        data={"vendor_name": "Test Vendor"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["offers_count"] == 1

    saved_files = list(tmp_path.iterdir())
    assert saved_files, "uploaded file was not written to storage"

    session.expire_all()
    document = session.exec(select(models.SourceDocument)).one()
    assert document.file_name == "sample.csv"
    assert document.status in {"processed", "processed_with_warnings"}
    assert document.ingest_started_at is not None
    assert document.ingest_started_at.tzinfo is None
    assert document.ingest_completed_at is not None
    assert document.ingest_completed_at.tzinfo is None

    offers = session.exec(select(models.Offer)).all()
    assert len(offers) == 1
    assert offers[0].price == 9.99
    assert offers[0].vendor.name == "Test Vendor"
    assert offers[0].captured_at.tzinfo is None

    app.dependency_overrides.pop(get_db, None)


def test_root_redirects_to_upload():
    client = TestClient(app)
    response = client.get("/", follow_redirects=False)

    assert response.status_code in (302, 303, 307)
    assert response.headers.get("location") == "/upload"


def test_upload_handles_weird_filename(monkeypatch, tmp_path, session):
    app.dependency_overrides[get_db] = _override_get_db(session)
    monkeypatch.setattr(settings, "ingestion_storage_dir", tmp_path)

    client = TestClient(app)
    weird_name = "../../Price List (Final) 2025!!.CSV"
    response = client.post(
        "/documents/upload",
        files={"file": (weird_name, "Product,Price\nWidget,9.99\n", "text/csv")},
        data={"vendor_name": "Odd Vendor"},
    )

    assert response.status_code == 200

    session.expire_all()
    document = session.exec(
        select(models.SourceDocument).order_by(models.SourceDocument.ingest_started_at.desc())
    ).first()
    assert document is not None
    assert document.file_name == Path(weird_name).name
    assert document.storage_path

    stored_path = Path(document.storage_path)
    assert stored_path.exists()
    assert stored_path.parent == tmp_path.resolve()

    app.dependency_overrides.pop(get_db, None)
