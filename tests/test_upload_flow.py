import pathlib
from pathlib import Path
from contextlib import contextmanager

from datetime import datetime
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.api.deps import get_db
from app.api.routes import documents as documents_route
from app.core.config import settings
from app.db import models
from app.main import app
from app.services import ingestion_jobs
from app.services.ingestion_jobs import ingestion_job_runner


def _override_get_db(session):
    def _get_db():
        yield session
    return _get_db


def _enable_sync_ingestion(monkeypatch, session):
    engine = session.get_bind()

    @contextmanager
    def _job_session_override():
        job_session = Session(engine)
        try:
            yield job_session
            job_session.commit()
        except Exception:
            job_session.rollback()
            raise
        finally:
            job_session.close()

    def _run_immediately(job_id):
        ingestion_job_runner._run_job_sync(job_id)

    monkeypatch.setattr(ingestion_jobs, "get_session", _job_session_override)
    monkeypatch.setattr(ingestion_job_runner, "enqueue", _run_immediately)


def test_upload_endpoint_persists_document(monkeypatch, tmp_path, session):
    _enable_sync_ingestion(monkeypatch, session)
    app.dependency_overrides[get_db] = _override_get_db(session)
    monkeypatch.setattr(settings, "ingestion_storage_dir", tmp_path)

    client = TestClient(app)
    file_content = "Product,Price\nWidget,9.99\n"
    response = client.post(
        "/documents/upload",
        files={"file": ("sample.csv", file_content, "text/csv")},
        data={"vendor_name": "Test Vendor"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["accepted_count"] == 1
    assert payload["failed_count"] == 0
    assert len(payload["accepted"]) == 1
    job_info = payload["accepted"][0]
    assert job_info["job_id"]
    assert job_info["document_id"]

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
    _enable_sync_ingestion(monkeypatch, session)
    app.dependency_overrides[get_db] = _override_get_db(session)
    monkeypatch.setattr(settings, "ingestion_storage_dir", tmp_path)

    client = TestClient(app)
    weird_name = "../../Price List (Final) 2025!!.CSV"
    response = client.post(
        "/documents/upload",
        files={"file": (weird_name, "Product,Price\nWidget,9.99\n", "text/csv")},
        data={"vendor_name": "Odd Vendor"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "accepted"

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


def test_upload_returns_clear_error_when_storage_unwritable(monkeypatch, tmp_path, session):
    app.dependency_overrides[get_db] = _override_get_db(session)
    target_dir = tmp_path / "blocked" / "nested"
    monkeypatch.setattr(settings, "ingestion_storage_dir", target_dir)

    original_mkdir = pathlib.Path.mkdir

    def fail_mkdir(self, *args, **kwargs):
        if self == target_dir:
            raise PermissionError("read-only path")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "mkdir", fail_mkdir)

    client = TestClient(app)
    response = client.post(
        "/documents/upload",
        files={"file": ("sample.csv", 'Product,Price\nWidget,9.99\n', "text/csv")},
        data={"vendor_name": "Vendor"},
    )

    assert response.status_code == 500
    body = response.json()
    assert "not writable" in body["detail"].lower()

    app.dependency_overrides.pop(get_db, None)


def test_upload_handles_generic_database_error(monkeypatch, tmp_path, session):
    app.dependency_overrides[get_db] = _override_get_db(session)
    monkeypatch.setattr(settings, "ingestion_storage_dir", tmp_path)

    original_commit = session.commit
    call_count = {"count": 0}

    def failing_commit():
        call_count["count"] += 1
        if call_count["count"] == 1:
            raise OperationalError("INSERT", {}, Exception("boom"))
        return original_commit()

    session.commit = failing_commit  # type: ignore[assignment]

    client = TestClient(app)
    response = client.post(
        "/documents/upload",
        files={"file": ("sample.csv", 'Product,Price\nWidget,9.99\n', "text/csv")},
        data={"vendor_name": "Vendor"},
    )

    assert response.status_code == 500
    body = response.json()
    assert "ingestion job metadata" in body["detail"].lower() or "document metadata" in body["detail"].lower()
    assert not any(tmp_path.iterdir())

    session.commit = original_commit
    app.dependency_overrides.pop(get_db, None)

def test_upload_storage_filenames_unique(monkeypatch, tmp_path, session):
    _enable_sync_ingestion(monkeypatch, session)
    app.dependency_overrides[get_db] = _override_get_db(session)
    monkeypatch.setattr(settings, "ingestion_storage_dir", tmp_path)

    fixed_timestamp = datetime(2025, 1, 1, 12, 0, 0)
    monkeypatch.setattr(documents_route, "_utc_now", lambda: fixed_timestamp)

    uuid_values = iter([UUID(int=1), UUID(int=2), UUID(int=3)])
    monkeypatch.setattr(documents_route, "uuid4", lambda: next(uuid_values))

    client = TestClient(app)
    payload = {"vendor_name": "Same Second Vendor"}
    file_content = "Product,Price\nWidget,9.99\n"

    for _ in range(2):
        response = client.post(
            "/documents/upload",
            files={"file": ("duplicate.csv", file_content, "text/csv")},
            data=payload,
        )
        assert response.status_code == 202
        assert response.json()["status"] == "accepted"

    session.expire_all()
    documents = session.exec(select(models.SourceDocument)).all()
    assert len(documents) == 2

    storage_names = {Path(doc.storage_path).name for doc in documents}
    assert len(storage_names) == 2

    for doc in documents:
        stored_path = Path(doc.storage_path)
        assert stored_path.exists()
        assert stored_path.parent == tmp_path.resolve()

    app.dependency_overrides.pop(get_db, None)


def test_upload_multiple_files_single_request(monkeypatch, tmp_path, session):
    _enable_sync_ingestion(monkeypatch, session)
    app.dependency_overrides[get_db] = _override_get_db(session)
    monkeypatch.setattr(settings, "ingestion_storage_dir", tmp_path)

    client = TestClient(app)
    files_payload = [
        ("files", ("sample1.csv", "Product,Price\nWidget,9.99\n", "text/csv")),
        ("files", ("sample2.csv", "Product,Price\nGadget,19.99\n", "text/csv")),
    ]

    response = client.post(
        "/documents/upload",
        files=files_payload,
        data={"vendor_name": "Multi Vendor"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["accepted_count"] == 2
    assert payload["failed_count"] == 0
    assert len(payload["accepted"]) == 2
    assert payload["failed"] == []

    session.expire_all()
    documents = session.exec(select(models.SourceDocument).order_by(models.SourceDocument.file_name)).all()
    assert len(documents) == 2
    assert {doc.file_name for doc in documents} == {"sample1.csv", "sample2.csv"}

    offers = session.exec(select(models.Offer)).all()
    assert len(offers) == 2

    app.dependency_overrides.pop(get_db, None)


def test_upload_multiple_files_partial_failure(monkeypatch, tmp_path, session):
    _enable_sync_ingestion(monkeypatch, session)
    app.dependency_overrides[get_db] = _override_get_db(session)
    monkeypatch.setattr(settings, "ingestion_storage_dir", tmp_path)

    client = TestClient(app)
    files_payload = [
        ("files", ("good.csv", "Product,Price\nWidget,9.99\n", "text/csv")),
        ("files", ("bad.exe", b"binary", "application/octet-stream")),
    ]

    response = client.post(
        "/documents/upload",
        files=files_payload,
        data={"vendor_name": "Partial Vendor"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "partial"
    assert payload["accepted_count"] == 1
    assert payload["failed_count"] == 1
    assert len(payload["accepted"]) == 1
    assert len(payload["failed"]) == 1
    assert "Unsupported file type" in payload["failed"][0]["detail"]

    session.expire_all()
    documents = session.exec(select(models.SourceDocument)).all()
    assert len(documents) == 1
    assert documents[0].file_name == "good.csv"

    offers = session.exec(select(models.Offer)).all()
    assert len(offers) == 1

    app.dependency_overrides.pop(get_db, None)
