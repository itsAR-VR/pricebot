from io import BytesIO
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.config import settings
from app.main import app


def _override_get_db(session):
    def _get_db():
        yield session
    return _get_db


def test_upload_then_chat_flow(session, tmp_path):
    """End-to-end: upload CSV → ingest → resolve → best-price."""

    # Use a temporary storage directory so the test is hermetic.
    original_storage = settings.ingestion_storage_dir
    settings.ingestion_storage_dir = tmp_path

    try:
        app.dependency_overrides[get_db] = _override_get_db(session)
        client = TestClient(app)

        # 1) Upload a simple CSV as a vendor price sheet
        csv_bytes = BytesIO(b"description,price,qty\nPixel 8 128GB,520,5\n")
        response = client.post(
            "/documents/upload",
            files={"file": ("e2e.csv", csv_bytes, "text/csv")},
            data={"vendor_name": "E2E Vendor", "processor": "spreadsheet"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "success"
        assert payload["offers_count"] == 1
        doc_id = None
        if payload.get("document_id"):
            doc_id = payload["document_id"]
            assert payload["status"] in {"processed", "processed_with_warnings"}
        elif isinstance(payload.get("processed"), list) and payload["processed"]:
            doc_id = payload["processed"][0]["document_id"]
            assert payload["processed"][0]["status"] in {"processed", "processed_with_warnings"}
        assert doc_id, payload

        # 2) Resolve the product from chat tools
        resolve = client.post(
            "/chat/tools/products/resolve",
            json={"query": "pixel 8", "limit": 5},
        )
        assert resolve.status_code == 200, resolve.text
        resolved = resolve.json()
        assert resolved["total"] >= 1
        product_name = resolved["products"][0]["canonical_name"].lower()
        assert "pixel" in product_name

        # 3) Best-price search should surface the uploaded offer
        best = client.post(
            "/chat/tools/offers/search-best-price",
            json={"query": "pixel 8 128gb", "limit": 3},
        )
        assert best.status_code == 200, best.text
        best_payload = best.json()
        assert len(best_payload["results"]) >= 1

        first_bundle = best_payload["results"][0]
        assert first_bundle["best_offer"]["price"] == 520
        assert first_bundle["best_offer"]["vendor"]["name"] == "E2E Vendor"
    finally:
        app.dependency_overrides.pop(get_db, None)
        settings.ingestion_storage_dir = original_storage
