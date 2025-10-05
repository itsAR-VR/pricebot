from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.db import models
from app.main import app


def _override_get_db(session):
    def _get_db():
        yield session
    return _get_db


def test_resolve_products_returns_matches(session):
    vendor = models.Vendor(name="Vendor X")
    product = models.Product(
        canonical_name="iPhone 17 Pro 256GB",
        model_number="IP17PRO-256",
        upc="123456789012",
        spec={"image_url": "https://example.com/iphone17.png"},
    )
    alias = models.ProductAlias(product=product, alias_text="IPHONE 17 PRO")
    session.add(vendor)
    session.add(product)
    session.add(alias)
    session.commit()

    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)

    response = client.post(
        "/chat/tools/products/resolve",
        json={"query": "iphone 17", "limit": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["limit"] == 3
    assert payload["offset"] == 0
    assert payload["total"] == 1
    assert payload["has_more"] is False
    assert payload["products"][0]["canonical_name"] == "iPhone 17 Pro 256GB"
    assert payload["products"][0]["match_source"] in {"canonical_name", "alias"}

    app.dependency_overrides.pop(get_db, None)


def test_search_best_price_returns_best_offer(session):
    vendor = models.Vendor(name="Vendor Y", contact_info={"phone": "+1-111-111"})
    product = models.Product(canonical_name="Samsung Galaxy Z Fold 6", spec={"image": "https://example.com/fold6.jpg"})
    document = models.SourceDocument(
        file_name="fold.xlsx",
        file_type="spreadsheet",
        storage_path="/tmp/fold.xlsx",
        status="processed",
        ingest_started_at=datetime.now(timezone.utc),
        ingest_completed_at=datetime.now(timezone.utc),
    )
    session.add_all([vendor, product, document])
    session.flush()

    offer_expensive = models.Offer(
        product_id=product.id,
        vendor_id=vendor.id,
        source_document_id=document.id,
        price=1899.0,
        currency="USD",
        captured_at=datetime(2024, 10, 1, tzinfo=timezone.utc),
        quantity=5,
        condition="New",
    )
    offer_cheaper = models.Offer(
        product_id=product.id,
        vendor_id=vendor.id,
        source_document_id=document.id,
        price=1799.0,
        currency="USD",
        captured_at=datetime(2024, 10, 2, tzinfo=timezone.utc),
        quantity=3,
        condition="New",
        location="Miami",
    )
    session.add_all([offer_expensive, offer_cheaper])
    session.commit()

    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)

    response = client.post(
        "/chat/tools/offers/search-best-price",
        json={"query": "galaxy fold 6", "limit": 5},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["has_more"] is False
    assert len(data["results"]) == 1
    best_offer = data["results"][0]["best_offer"]
    assert best_offer["price"] == 1799.0
    assert best_offer["vendor"]["name"] == "Vendor Y"
    assert best_offer["source_document"]["file_name"] == "fold.xlsx"
    assert len(data["results"][0]["alternate_offers"]) == 1

    app.dependency_overrides.pop(get_db, None)


def test_resolve_products_rejects_blank_query(session):
    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)

    response = client.post(
        "/chat/tools/products/resolve",
        json={"query": "   ", "limit": 3},
    )

    assert response.status_code == 422

    app.dependency_overrides.pop(get_db, None)


def test_search_best_price_missing_vendor_returns_404(session):
    product = models.Product(canonical_name="Pixel 10 Pro")
    session.add(product)
    session.commit()

    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)

    response = client.post(
        "/chat/tools/offers/search-best-price",
        json={
            "query": "pixel",
            "limit": 2,
            "filters": {"vendor_id": str(uuid4())},
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Vendor not found"

    app.dependency_overrides.pop(get_db, None)


def test_search_best_price_applies_filters(session):
    vendor_a = models.Vendor(name="Vendor A")
    vendor_b = models.Vendor(name="Vendor B")
    product = models.Product(canonical_name="Surface Laptop 7")
    document = models.SourceDocument(
        file_name="surface.xlsx",
        file_type="spreadsheet",
        storage_path="/tmp/surface.xlsx",
        status="processed",
        ingest_started_at=datetime.now(timezone.utc),
        ingest_completed_at=datetime.now(timezone.utc),
    )
    session.add_all([vendor_a, vendor_b, product, document])
    session.flush()

    offer_old = models.Offer(
        product_id=product.id,
        vendor_id=vendor_a.id,
        source_document_id=document.id,
        price=1199.0,
        currency="USD",
        captured_at=datetime.now(timezone.utc) - timedelta(days=30),
        condition="New",
        location="Warehouse A",
    )
    offer_recent_match = models.Offer(
        product_id=product.id,
        vendor_id=vendor_a.id,
        source_document_id=document.id,
        price=1299.0,
        currency="USD",
        captured_at=datetime.now(timezone.utc),
        condition="New",
        location="New York",
    )
    offer_other_vendor = models.Offer(
        product_id=product.id,
        vendor_id=vendor_b.id,
        source_document_id=document.id,
        price=999.0,
        currency="USD",
        captured_at=datetime.now(timezone.utc),
        condition="Refurbished",
        location="New York",
    )
    session.add_all([offer_old, offer_recent_match, offer_other_vendor])
    session.commit()

    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)

    response = client.post(
        "/chat/tools/offers/search-best-price",
        json={
            "query": "surface laptop",
            "limit": 3,
            "filters": {
                "vendor_id": str(vendor_a.id),
                "condition": "New",
                "location": "New York",
                "min_price": 1200,
                "max_price": 1300,
                "captured_since": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 1
    best_offer = data["results"][0]["best_offer"]
    assert best_offer["price"] == 1299.0
    assert best_offer["vendor"]["name"] == "Vendor A"
    assert data["results"][0]["alternate_offers"] == []

    app.dependency_overrides.pop(get_db, None)


def test_resolve_products_paginates_results(session):
    vendor = models.Vendor(name="Vendor X")
    products = [
        models.Product(canonical_name=f"Test Product {idx}")
        for idx in range(5)
    ]
    session.add(vendor)
    session.add_all(products)
    session.commit()

    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)

    response_page_one = client.post(
        "/chat/tools/products/resolve",
        json={"query": "Test Product", "limit": 2, "offset": 0},
    )
    assert response_page_one.status_code == 200
    data_page_one = response_page_one.json()
    assert data_page_one["limit"] == 2
    assert data_page_one["offset"] == 0
    assert data_page_one["has_more"] is True
    assert data_page_one["next_offset"] == 2

    response_page_two = client.post(
        "/chat/tools/products/resolve",
        json={"query": "Test Product", "limit": 2, "offset": data_page_one["next_offset"]},
    )
    assert response_page_two.status_code == 200
    data_page_two = response_page_two.json()
    assert data_page_two["offset"] == 2
    assert data_page_two["limit"] == 2
    assert data_page_two["has_more"] is True
    assert data_page_two["next_offset"] == 4

    response_page_three = client.post(
        "/chat/tools/products/resolve",
        json={"query": "Test Product", "limit": 2, "offset": data_page_two["next_offset"]},
    )
    assert response_page_three.status_code == 200
    data_page_three = response_page_three.json()
    assert data_page_three["offset"] == 4
    assert data_page_three["has_more"] is False
    assert data_page_three["next_offset"] is None

    app.dependency_overrides.pop(get_db, None)
