from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.db import models
from app.core.log_buffer import reset_buffers
from app.services.help_index import reset_help_index_cache
from app.main import app
from app.services.llm_extraction import OfferLLMExtractor


def _override_get_db(session):
    def _get_db():
        yield session
    return _get_db


def test_diagnostics_endpoint_returns_counts(session):
    vendor = models.Vendor(name="Vendor Diagnostics")
    product = models.Product(canonical_name="Pixel 9 Pro")
    document = models.SourceDocument(
        file_name="diagnostics.xlsx",
        file_type="spreadsheet",
        storage_path="/tmp/diagnostics.xlsx",
        status="processed_with_warnings",
        ingest_started_at=datetime.now(timezone.utc),
        ingest_completed_at=datetime.now(timezone.utc),
        extra={"ingestion_errors": ["missing price column"]},
        vendor_id=None,
    )
    session.add_all([vendor, product, document])
    session.flush()

    offer = models.Offer(
        product_id=product.id,
        vendor_id=vendor.id,
        source_document_id=document.id,
        price=999.0,
        currency="USD",
        captured_at=datetime.now(timezone.utc),
        quantity=5,
    )
    session.add(offer)
    session.commit()

    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)

    response = client.get("/chat/tools/diagnostics")
    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["vendors"] == 1
    assert payload["counts"]["products"] == 1
    assert payload["counts"]["offers"] == 1
    assert payload["counts"]["documents"] == 1
    assert payload["health"]["status"] == "ok"
    assert payload["feature_flags"]["environment"] == payload["metadata"]["environment"]

    recent_docs = payload["recent_documents"]
    assert recent_docs
    doc_entry = next(item for item in recent_docs if item["id"] == str(document.id))
    assert "missing price column" in doc_entry["ingestion_errors"]
    assert payload["ingestion_warnings"]
    assert "missing price column" in payload["ingestion_warnings"][0]["messages"][0]
    assert isinstance(payload.get("whatsapp_metrics"), list)
    assert isinstance(payload.get("whatsapp_media_failures"), list)

    download = client.get("/chat/tools/diagnostics/download")
    assert download.status_code == 200
    assert "attachment" in download.headers.get("content-disposition", "")
    downloaded_payload = download.json()
    assert downloaded_payload["counts"] == payload["counts"]

    app.dependency_overrides.pop(get_db, None)


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


def test_logs_endpoint_returns_recent_entries(session):
    reset_buffers()
    logger = logging.getLogger("pricebot.tests")
    logger.info("captured log entry for buffer")

    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)
    try:
        response = client.get("/chat/tools/diagnostics")
        assert response.status_code == 200

        logs_response = client.get("/chat/tools/logs")
        assert logs_response.status_code == 200
        payload = logs_response.json()
        assert payload["logs"]
        assert any(
            entry["message"] == "captured log entry for buffer"
            for entry in payload["logs"]
        )
        assert payload["tool_calls"]
        tool_call = payload["tool_calls"][-1]
        assert tool_call["path"] == "/chat/tools/diagnostics"
        assert tool_call["method"] == "GET"
        assert tool_call["status"] == 200
        assert tool_call["duration_ms"] >= 0

        download = client.get("/chat/tools/logs/download")
        assert download.status_code == 200
        assert "attachment" in download.headers.get("content-disposition", "")
    finally:
        app.dependency_overrides.pop(get_db, None)
        reset_buffers()


def test_help_endpoint_returns_answer():
    reset_help_index_cache()
    client = TestClient(app)

    response = client.post("/chat/tools/help", json={"query": "what is ai normalization"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"]
    assert "ai normalization" in payload["answer"].lower()
    assert isinstance(payload["used_llm"], bool)
    assert payload["sources"]
    assert any("HELP_TOPICS" in source["path"] for source in payload["sources"])


def test_diagnostics_includes_logs_and_versions(session):
    reset_buffers()
    logging.getLogger("pricebot.diagnostics").warning("diagnostics inline log")

    vendor = models.Vendor(name="Diagnostics Vendor")
    session.add(vendor)
    session.commit()

    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)
    try:
        response = client.get(
            "/chat/tools/diagnostics",
            params={"include": "logs,versions", "logs_limit": 5},
        )
        assert response.status_code == 200
        payload = response.json()
        logs_tail = payload.get("logs_tail")
        assert logs_tail, payload
        assert any(entry["message"] == "diagnostics inline log" for entry in logs_tail)

        versions = payload.get("versions")
        assert versions
        assert versions["packages"]["fastapi"]
        assert versions["packages"]["sqlmodel"]
        assert versions["llm"]["default_model"] == OfferLLMExtractor.DEFAULT_MODEL
        assert versions["feature_flags"]["enable_openai"] == payload["feature_flags"]["enable_openai"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        reset_buffers()


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


def test_export_best_price_csv(session):
    vendor = models.Vendor(name="Vendor Y")
    product = models.Product(canonical_name="Exportable Product")
    document = models.SourceDocument(
        file_name="export.xlsx",
        file_type="spreadsheet",
        storage_path="/tmp/export.xlsx",
        status="processed",
    )
    session.add_all([vendor, product, document])
    session.flush()

    offer = models.Offer(
        product_id=product.id,
        vendor_id=vendor.id,
        source_document_id=document.id,
        price=123.45,
        currency="USD",
        captured_at=datetime.now(timezone.utc),
    )
    session.add(offer)
    session.commit()

    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)

    response = client.post(
        "/chat/tools/offers/export",
        json={"query": "Exportable", "limit": 5, "offset": 0, "filters": {}},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    content = response.text
    assert "product_name" in content.splitlines()[0]
    assert "Exportable Product" in content
    assert "Vendor Y" in content
    assert "123.45" in content
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


def test_search_best_price_empty_results_include_recent_products(session):
    vendor = models.Vendor(name="Vendor Q")
    product_recent = models.Product(canonical_name="Recent Product")
    product_old = models.Product(canonical_name="Older Product")
    session.add_all([vendor, product_recent, product_old])
    session.flush()

    recent_document = models.SourceDocument(
        file_name="recent.xlsx",
        file_type="spreadsheet",
        storage_path="/tmp/recent.xlsx",
        status="processed",
        ingest_completed_at=datetime.now(timezone.utc),
    )
    old_document = models.SourceDocument(
        file_name="old.xlsx",
        file_type="spreadsheet",
        storage_path="/tmp/old.xlsx",
        status="processed",
        ingest_completed_at=datetime.now(timezone.utc),
    )
    session.add_all([recent_document, old_document])
    session.flush()

    recent_offer = models.Offer(
        product_id=product_recent.id,
        vendor_id=vendor.id,
        source_document_id=recent_document.id,
        price=99.0,
        currency="USD",
        captured_at=datetime.now(timezone.utc),
    )
    older_offer = models.Offer(
        product_id=product_old.id,
        vendor_id=vendor.id,
        source_document_id=old_document.id,
        price=149.0,
        currency="USD",
        captured_at=datetime.now(timezone.utc) - timedelta(days=7),
    )
    session.add_all([recent_offer, older_offer])
    session.commit()

    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)

    response = client.post(
        "/chat/tools/offers/search-best-price",
        json={"query": "non matching query", "limit": 3},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"] == []
    assert len(data["recent_products"]) == 2
    assert data["recent_products"][0]["canonical_name"] == "Recent Product"
    assert data["recent_products"][1]["canonical_name"] == "Older Product"
    assert data["recent_products"][0]["offer_count"] == 1

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
