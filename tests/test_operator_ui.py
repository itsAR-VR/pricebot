from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.db import models
from app.main import app


def _override_get_db(session):
    def _get_db():
        yield session
    return _get_db


def test_operator_dashboard_renders(session):
    app.dependency_overrides[get_db] = _override_get_db(session)

    doc = models.SourceDocument(
        file_name="sheet.xlsx",
        file_type="spreadsheet",
        storage_path="/tmp/sheet.xlsx",
        status="processed",
        ingest_started_at=datetime.now(timezone.utc),
        ingest_completed_at=datetime.now(timezone.utc),
    )
    session.add(doc)
    session.commit()

    client = TestClient(app)
    response = client.get("/admin/documents")

    assert response.status_code == 200
    assert "sheet.xlsx" in response.text

    app.dependency_overrides.pop(get_db, None)


def test_operator_document_detail(session):
    app.dependency_overrides[get_db] = _override_get_db(session)

    vendor = models.Vendor(name="Vendor A")
    product = models.Product(canonical_name="MacBook Air")
    document = models.SourceDocument(
        file_name="offer.pdf",
        file_type="document_text",
        storage_path="/tmp/offer.pdf",
        status="processed",
        ingest_started_at=datetime.now(timezone.utc),
        ingest_completed_at=datetime.now(timezone.utc),
    )
    session.add(vendor)
    session.add(product)
    session.add(document)
    session.flush()

    offer = models.Offer(
        product_id=product.id,
        vendor_id=vendor.id,
        source_document_id=document.id,
        price=999.0,
        currency="USD",
        captured_at=datetime.now(timezone.utc),
    )
    session.add(offer)
    session.commit()

    client = TestClient(app)
    response = client.get(f"/admin/documents/{document.id}")

    assert response.status_code == 200
    assert "MacBook Air" in response.text

    app.dependency_overrides.pop(get_db, None)
