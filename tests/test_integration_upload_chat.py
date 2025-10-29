from contextlib import contextmanager
from io import BytesIO
import time

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_db
from app.core.config import settings
from app.main import app
from app.services import ingestion_jobs
from app.services.ingestion_jobs import ingestion_job_runner


def _override_get_db(session):
    def _get_db():
        yield session
    return _get_db


def test_upload_then_chat_flow(session, tmp_path):
    """End-to-end: upload CSV → ingest → resolve → best-price."""

    # Use a temporary storage directory so the test is hermetic.
    original_storage = settings.ingestion_storage_dir
    original_enqueue = ingestion_job_runner.enqueue
    original_get_session = ingestion_jobs.get_session
    settings.ingestion_storage_dir = tmp_path

    try:
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

        ingestion_jobs.get_session = _job_session_override
        ingestion_job_runner.enqueue = lambda job_id: ingestion_job_runner._run_job_sync(job_id)

        app.dependency_overrides[get_db] = _override_get_db(session)
        client = TestClient(app)

        # 1) Upload a simple CSV as a vendor price sheet
        csv_bytes = BytesIO(b"description,price,qty\nPixel 8 128GB,520,5\n")
        response = client.post(
            "/documents/upload",
            files={"file": ("e2e.csv", csv_bytes, "text/csv")},
            data={"vendor_name": "E2E Vendor", "processor": "spreadsheet"},
        )
        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["status"] in {"accepted", "partial"}
        accepted = payload.get("accepted") or []
        assert accepted, payload
        job_info = accepted[0]
        job_id = job_info["job_id"]
        doc_id = job_info["document_id"]
        assert job_id and doc_id, payload

        # Wait for the background job to finish
        job_payload = None
        for _ in range(20):
            session.expire_all()
            job_response = client.get(f"/documents/jobs/{job_id}")
            assert job_response.status_code == 200, job_response.text
            job_payload = job_response.json()
            if job_payload["status"] in {"processed", "processed_with_warnings", "failed"}:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("Ingestion job did not complete")

        assert job_payload is not None
        assert job_payload["status"] in {"processed", "processed_with_warnings"}

        detail_response = client.get(f"/documents/{doc_id}")
        assert detail_response.status_code == 200
        detail_payload = detail_response.json()
        assert detail_payload["status"] in {"processed", "processed_with_warnings"}
        assert detail_payload["offer_count"] == 1

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
        ingestion_job_runner.enqueue = original_enqueue
        ingestion_jobs.get_session = original_get_session
        settings.ingestion_storage_dir = original_storage
