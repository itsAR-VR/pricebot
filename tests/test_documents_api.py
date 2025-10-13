from datetime import datetime, timezone
from io import BytesIO

import pandas as pd

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_db
from app.db import models
from app.main import app


def _override_get_db(session: Session):
    def _get_db():
        yield session
    return _get_db


def test_vendor_template_generates_excel_when_missing(tmp_path):
    from app.api.routes import documents as documents_routes

    original_template_path = documents_routes.TEMPLATE_PATH
    documents_routes.TEMPLATE_PATH = tmp_path / "vendor_price_template.xlsx"
    try:
        client = TestClient(app)
        response = client.get("/documents/templates/vendor-price")
    finally:
        documents_routes.TEMPLATE_PATH = original_template_path

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    workbook = BytesIO(response.content)
    frame = pd.read_excel(workbook)
    assert list(frame.columns) == ["Item", "Price", "Qty", "Condition", "Location", "Notes"]

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

    assert response.status_code == 200, (response.json(), str(response.request.url))
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

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["file_name"] == "sheet.xlsx"
    assert data["offer_count"] == 1
    assert len(data["offers"]) == 1
    assert data["offers"][0]["vendor_name"] == "Vendor A"

    app.dependency_overrides.pop(get_db, None)



def test_ingest_document_force(session, tmp_path):
    app.dependency_overrides[get_db] = _override_get_db(session)

    csv_path = tmp_path / "offers.csv"
    csv_path.write_text("description,price\nPixel 8 128GB,520\n")

    source_doc = models.SourceDocument(
        file_name="offers.csv",
        file_type=".csv",
        storage_path=str(csv_path),
        status="failed",
        extra={"processor": "spreadsheet", "declared_vendor": "Cellntell"},
    )
    session.add(source_doc)
    session.commit()

    client = TestClient(app)
    response = client.post(
        f"/documents/{source_doc.id}/ingest",
        json={"force": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["offers_count"] == 1
    assert data["status"] in {"processed", "processed_with_warnings"}

    offers = session.exec(select(models.Offer).where(models.Offer.source_document_id == source_doc.id)).all()
    assert len(offers) == 1
    assert offers[0].price == 520
    assert offers[0].vendor.name == "Cellntell"

    app.dependency_overrides.pop(get_db, None)



def test_related_documents(session):
    app.dependency_overrides[get_db] = _override_get_db(session)

    vendor = models.Vendor(name="Vendor B")
    product = models.Product(canonical_name="Galaxy S24")
    document_a = models.SourceDocument(
        file_name="sheet.csv",
        file_type=".csv",
        storage_path="/tmp/sheet.csv",
        status="processed",
        extra={},
    )
    document_b = models.SourceDocument(
        file_name="sheet-two.csv",
        file_type=".csv",
        storage_path="/tmp/sheet-two.csv",
        status="processed",
        extra={},
    )
    session.add(vendor)
    session.add(product)
    session.add(document_a)
    session.add(document_b)
    session.flush()

    offer_a = models.Offer(
        product_id=product.id,
        vendor_id=vendor.id,
        price=800.0,
        currency="USD",
        captured_at=datetime.now(timezone.utc),
        source_document_id=document_a.id,
    )
    offer_b = models.Offer(
        product_id=product.id,
        vendor_id=vendor.id,
        price=810.0,
        currency="USD",
        captured_at=datetime.now(timezone.utc),
        source_document_id=document_b.id,
    )
    session.add(offer_a)
    session.add(offer_b)
    session.commit()

    client = TestClient(app)
    response = client.get(
        "/documents/related",
        params={"offer_ids": [str(offer_a.id), str(offer_b.id)]},
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert len(data) == 2

    data_by_id = {item["id"]: item for item in data}
    assert set(data_by_id) == {str(document_a.id), str(document_b.id)}
    assert data_by_id[str(document_a.id)]["offer_ids"] == [str(offer_a.id)]
    assert data_by_id[str(document_b.id)]["offer_ids"] == [str(offer_b.id)]

    app.dependency_overrides.pop(get_db, None)
