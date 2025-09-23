from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_db
from app.db import models
from app.main import app


def _override_get_db(session: Session):
    def _get_db():
        yield session
    return _get_db


def test_list_documents(session):
    app.dependency_overrides[get_db] = _override_get_db(session)

    document = models.SourceDocument(
        file_name="sheet.xlsx",
        file_type="spreadsheet",
        storage_path="/tmp/sheet.xlsx",
        status="processed",
        ingest_started_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        ingest_completed_at=datetime(2024, 1, 1, 12, 5, tzinfo=timezone.utc),
        extra={"processor": "spreadsheet"},
    )
    session.add(document)
    session.commit()

    client = TestClient(app)
    response = client.get("/documents")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["file_name"] == "sheet.xlsx"
    assert data[0]["offer_count"] == 0

    app.dependency_overrides.pop(get_db, None)


def test_get_document_detail(session):
    app.dependency_overrides[get_db] = _override_get_db(session)

    vendor = models.Vendor(name="Vendor A")
    product = models.Product(canonical_name="iPhone 15")
    document = models.SourceDocument(
        file_name="sheet.xlsx",
        file_type="spreadsheet",
        storage_path="/tmp/sheet.xlsx",
        status="processed",
        ingest_started_at=datetime.now(timezone.utc),
        ingest_completed_at=datetime.now(timezone.utc),
        extra={},
        vendor_id=None,
    )
    session.add(vendor)
    session.add(product)
    session.add(document)
    session.flush()

    offer = models.Offer(
        product_id=product.id,
        vendor_id=vendor.id,
        price=100.0,
        currency="USD",
        captured_at=datetime.now(timezone.utc),
        source_document_id=document.id,
    )
    session.add(offer)
    session.commit()

    client = TestClient(app)
    response = client.get(f"/documents/{document.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["file_name"] == "sheet.xlsx"
    assert data["offer_count"] == 1
    assert len(data["offers"]) == 1
    assert data["offers"][0]["vendor_name"] == "Vendor A"

    app.dependency_overrides.pop(get_db, None)
