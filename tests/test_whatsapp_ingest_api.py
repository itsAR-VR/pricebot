from datetime import datetime
import hashlib
import hmac
import json
import time
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_session
from app.db import models
from sqlmodel import select
from app.core.config import settings
from app.api.routes import integrations_whatsapp
from app.core.metrics import metrics
from app.services.whatsapp_scheduler import scheduler
from app.services.ingestion_jobs import ingestion_job_runner


def _reset_media_storage(document):
    try:
        from pathlib import Path

        path = Path(document.storage_path)
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _purge_media_document(session, document):
    jobs = session.exec(
        select(models.IngestionJob).where(models.IngestionJob.source_document_id == document.id)
    ).all()
    for job in jobs:
        session.delete(job)
    _reset_media_storage(document)
    session.delete(document)


def test_ingest_whatsapp_messages_creates_rows():
    client = TestClient(app)

    title = f"Deals Group {uuid.uuid4()}"
    payload = {
        "client_id": "dev-client",
        "messages": [
            {
                "chat_title": title,
                "text": "Selling Pixel 8 128GB - $520 net",
                "observed_at": datetime.utcnow().isoformat() + "Z",
                "sender_name": "Sara",
                "is_outgoing": False,
            }
        ],
    }
    response = client.post(
        "/integrations/whatsapp/ingest",
        json=payload,
        headers={"x-ingest-token": "test-token"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "request_id" in data and data["request_id"]
    assert data["accepted"] == 1
    assert data["created"] == 1
    assert data["deduped"] == 0
    assert data["created_chats"] == 1
    assert data["decisions"][0]["status"] == "created"
    assert data["decisions"][0]["whatsapp_message_id"]

    # Verify persisted
    with get_session() as session:
        chat = session.exec(select(models.WhatsAppChat).where(models.WhatsAppChat.title == title)).first()
        assert chat is not None
        msgs = session.exec(select(models.WhatsAppMessage).where(models.WhatsAppMessage.chat_id == chat.id)).all()
        assert len(msgs) >= 1


def test_ingest_requires_token():
    client = TestClient(app)
    payload = {
        "client_id": "dev-client",
        "messages": [
            {
                "chat_title": "Missing Token Chat",
                "text": "Offer without token",
                "observed_at": datetime.utcnow().isoformat() + "Z",
            }
        ],
    }
    response = client.post("/integrations/whatsapp/ingest", json=payload)
    assert response.status_code == 401
    totals = metrics.aggregate_totals()
    assert totals["auth_failures"] == 1
    assert totals["http_4xx"] == 1
    recent = metrics.recent_failures()
    assert recent and recent[0].status_code == 401


def test_ingest_rejects_invalid_hmac():
    client = TestClient(app)
    payload = {
        "client_id": "dev-client",
        "messages": [
            {
                "chat_title": "HMAC Chat",
                "text": "Offer with invalid HMAC",
                "observed_at": datetime.utcnow().isoformat() + "Z",
            }
        ],
    }
    settings.whatsapp_ingest_hmac_secret = "super-secret"
    try:
        response = client.post(
            "/integrations/whatsapp/ingest",
            json=payload,
            headers={
                "x-ingest-token": "test-token",
                "x-signature": "deadbeef",
                "x-signature-timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )
        assert response.status_code == 403
    finally:
        settings.whatsapp_ingest_hmac_secret = None
    totals = metrics.aggregate_totals()
    assert totals["forbidden"] == 1
    assert totals["signature_failures"] == 1
    recent = metrics.recent_failures()
    assert recent and recent[0].status_code == 403


def test_ingest_accepts_valid_hmac():
    client = TestClient(app)
    payload = {
        "client_id": "dev-client",
        "messages": [
            {
                "chat_title": "HMAC Valid Chat",
                "text": "Offer with valid HMAC",
                "observed_at": datetime.utcnow().isoformat() + "Z",
            }
        ],
    }
    secret = "another-secret"
    timestamp = datetime.utcnow().isoformat() + "Z"
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()

    settings.whatsapp_ingest_hmac_secret = secret
    try:
        response = client.post(
            "/integrations/whatsapp/ingest",
            content=body,
            headers={
                "content-type": "application/json",
                "x-ingest-token": "test-token",
                "x-signature": signature,
                "x-signature-timestamp": timestamp,
            },
        )
        assert response.status_code == 200, response.text
    finally:
        settings.whatsapp_ingest_hmac_secret = None


def test_ingest_rate_limit_enforced():
    client = TestClient(app)
    payload = {
        "client_id": "rate-limit-client",
        "messages": [
            {
                "chat_title": "Rate Limit Chat",
                "text": "First offer",
            }
        ],
    }
    original_rate = settings.whatsapp_ingest_rate_limit_per_minute
    original_burst = settings.whatsapp_ingest_rate_limit_burst
    settings.whatsapp_ingest_rate_limit_per_minute = 1
    settings.whatsapp_ingest_rate_limit_burst = 1
    integrations_whatsapp._rate_limiter = integrations_whatsapp._create_rate_limiter()
    try:
        ok = client.post(
            "/integrations/whatsapp/ingest",
            json=payload,
            headers={"x-ingest-token": "test-token"},
        )
        assert ok.status_code == 200
        blocked = client.post(
            "/integrations/whatsapp/ingest",
            json=payload,
            headers={"x-ingest-token": "test-token"},
        )
        assert blocked.status_code == 429
        totals = metrics.aggregate_totals()
        assert totals["rate_limited"] == 1
        recent = metrics.recent_failures()
        assert recent and recent[0].status_code == 429
    finally:
        settings.whatsapp_ingest_rate_limit_per_minute = original_rate
        settings.whatsapp_ingest_rate_limit_burst = original_burst
        integrations_whatsapp._rate_limiter = integrations_whatsapp._create_rate_limiter()


def test_ingest_returns_per_message_decisions():
    client = TestClient(app)
    payload = {
        "client_id": "dev-client",
        "messages": [
            {
                "chat_title": "Decisions Chat",
                "text": "   ",
            },
            {
                "chat_title": "Decisions Chat",
                "text": "Selling MacBook Pro - $900",
            },
        ],
    }
    response = client.post(
        "/integrations/whatsapp/ingest",
        json=payload,
        headers={"x-ingest-token": "test-token"},
    )
    assert response.status_code == 200
    decisions = response.json()["decisions"]
    assert len(decisions) == 2
    statuses = {d["status"] for d in decisions}
    assert "skipped" in statuses
    assert any(status in {"created", "deduped"} for status in statuses)
    reasons = {d["reason"] for d in decisions if d.get("status") == "skipped"}
    assert "empty_text" in reasons


def test_metrics_endpoint_returns_snapshot():
    client = TestClient(app)
    payload = {
        "client_id": "metrics-client",
        "messages": [
            {
                "chat_title": "Metrics Chat",
                "text": "Surface Laptop 5 - $980",
            }
        ],
    }
    ok = client.post(
        "/integrations/whatsapp/ingest",
        json=payload,
        headers={"x-ingest-token": "test-token"},
    )
    assert ok.status_code == 200

    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert "whatsapp" in body
    whatsapp = body["whatsapp"]
    assert "totals" in whatsapp
    assert "counters" in whatsapp
    totals = metrics.aggregate_totals()
    assert whatsapp["totals"]["created"] == totals["created"]
    assert whatsapp["totals"]["accepted"] == totals["accepted"]
    assert isinstance(whatsapp["counters"], list)
    assert whatsapp["totals"]["auth_failures"] == 0
    assert whatsapp["recent_failures"] == []


def test_media_upload_creates_document_and_links_to_message():
    client = TestClient(app)
    message_id = f"media-msg-{uuid.uuid4()}"
    with get_session() as session:
        docs = session.exec(
            select(models.SourceDocument).where(models.SourceDocument.file_type == "whatsapp_media")
        ).all()
        for doc in docs:
            _purge_media_document(session, doc)
        chat = session.exec(
            select(models.WhatsAppChat).where(models.WhatsAppChat.title == "Media Upload Chat")
        ).first()
        if chat:
            messages = session.exec(
                select(models.WhatsAppMessage).where(models.WhatsAppMessage.chat_id == chat.id)
            ).all()
            for message in messages:
                session.delete(message)
            session.delete(chat)
    original_enqueue = ingestion_job_runner.enqueue
    ingestion_job_runner.enqueue = lambda job_id: None
    try:
        files = {"file": ("flyer.jpg", b"binary-data", "image/jpeg")}
        data = {
            "client_id": "dev-client",
            "chat_title": "Media Upload Chat",
            "message_id": message_id,
            "media_kind": "image",
            "caption": "Specials",
        }
        response = client.post(
            "/integrations/whatsapp/media",
            data=data,
            files=files,
            headers={"x-ingest-token": "test-token"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "queued"
        document_id = payload["document_id"]
        assert document_id

        with get_session() as session:
            document = session.get(models.SourceDocument, uuid.UUID(document_id))
            assert document is not None
            assert document.file_type == "whatsapp_media"
            assert document.source_whatsapp_message_id is None
            assert (document.extra or {}).get("media_kind") == "image"

        ingest_payload = {
            "client_id": "dev-client",
            "messages": [
                {
                    "chat_title": "Media Upload Chat",
                    "message_id": message_id,
                    "media": {
                        "document_id": document_id,
                        "mimetype": "image/jpeg",
                        "kind": "image",
                        "caption": "Specials",
                    },
                }
            ],
        }
        ingest_response = client.post(
            "/integrations/whatsapp/ingest",
            json=ingest_payload,
            headers={"x-ingest-token": "test-token"},
        )
        assert ingest_response.status_code == 200, ingest_response.text
        result = ingest_response.json()
        assert result["created"] == 1
        decision = result["decisions"][0]
        assert decision.get("media_document_id") == document_id

        with get_session() as session:
            document = session.get(models.SourceDocument, uuid.UUID(document_id))
            assert document is not None
            assert document.source_whatsapp_message_id is not None
            message = session.get(models.WhatsAppMessage, document.source_whatsapp_message_id)
            assert message is not None
            assert message.raw_payload is not None
            assert message.raw_payload.get("media", {}).get("document_id") == document_id
    finally:
        ingestion_job_runner.enqueue = original_enqueue
        if "document_id" in locals():
            with get_session() as session:
                doc = session.get(models.SourceDocument, uuid.UUID(document_id))
                if doc:
                    _purge_media_document(session, doc)


def test_media_upload_deduplicates_by_message_id():
    client = TestClient(app)
    message_id = f"media-msg-dup-{uuid.uuid4()}"
    with get_session() as session:
        docs = session.exec(
            select(models.SourceDocument).where(models.SourceDocument.file_type == "whatsapp_media")
        ).all()
        for doc in docs:
            _purge_media_document(session, doc)
        chat = session.exec(
            select(models.WhatsAppChat).where(models.WhatsAppChat.title == "Media Dedup Chat")
        ).first()
        if chat:
            messages = session.exec(
                select(models.WhatsAppMessage).where(models.WhatsAppMessage.chat_id == chat.id)
            ).all()
            for message in messages:
                session.delete(message)
            session.delete(chat)
    original_enqueue = ingestion_job_runner.enqueue
    ingestion_job_runner.enqueue = lambda job_id: None
    try:
        files = {"file": ("promo.pdf", b"binary-data", "application/pdf")}
        data = {
            "client_id": "dev-client",
            "chat_title": "Media Dedup Chat",
            "message_id": message_id,
        }
        first = client.post(
            "/integrations/whatsapp/media",
            data=data,
            files=files,
            headers={"x-ingest-token": "test-token"},
        )
        assert first.status_code == 200
        doc_id = first.json()["document_id"]

        second = client.post(
            "/integrations/whatsapp/media",
            data=data,
            files=files,
            headers={"x-ingest-token": "test-token"},
        )
        assert second.status_code == 200
        body = second.json()
        assert body["status"] == "deduped"
        assert body["document_id"] == doc_id
    finally:
        ingestion_job_runner.enqueue = original_enqueue
        if "doc_id" in locals():
            with get_session() as session:
                doc = session.get(models.SourceDocument, uuid.UUID(doc_id))
                if doc:
                    _purge_media_document(session, doc)


def test_media_upload_rejects_large_payload_with_metrics():
    client = TestClient(app)
    original_max = settings.whatsapp_media_max_bytes
    settings.whatsapp_media_max_bytes = 128
    try:
        files = {"file": ("large.bin", b"x" * 256, "application/octet-stream")}
        data = {
            "client_id": "dev-client",
            "chat_title": "Large Media Chat",
        }
        response = client.post(
            "/integrations/whatsapp/media",
            data=data,
            files=files,
            headers={"x-ingest-token": "test-token"},
        )
        assert response.status_code == 413
        totals = metrics.aggregate_totals()
        assert totals["media_failed"] == 1
        assert totals["http_4xx"] == 1
        recent = metrics.recent_failures()
        assert recent and recent[0].status_code == 413
        assert recent[0].reason == "media_too_large"
    finally:
        settings.whatsapp_media_max_bytes = original_max


def test_chat_vendor_mapping_endpoint():
    client = TestClient(app)
    with get_session() as session:
        vendor = models.Vendor(name=f"Vendor {uuid.uuid4()}")
        chat = models.WhatsAppChat(title=f"Chat {uuid.uuid4()}")
        session.add(vendor)
        session.add(chat)
        session.commit()
        chat_id = chat.id
        vendor_id = vendor.id

    response = client.put(
        f"/integrations/whatsapp/chats/{chat_id}/vendor",
        json={"vendor_id": str(vendor_id)},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["vendor_id"] == str(vendor_id)
    assert data["vendor_name"]

    with get_session() as session:
        refreshed = session.get(models.WhatsAppChat, chat_id)
        assert refreshed is not None
        assert refreshed.vendor_id == vendor_id

    # Clear mapping
    cleared = client.put(
        f"/integrations/whatsapp/chats/{chat_id}/vendor",
        json={"vendor_id": None},
    )
    assert cleared.status_code == 200
    cleared_data = cleared.json()
    assert cleared_data["vendor_id"] is None
    with get_session() as session:
        refreshed = session.get(models.WhatsAppChat, chat_id)
        assert refreshed is not None
        assert refreshed.vendor_id is None


def test_auto_extract_runs_after_debounce():
    client = TestClient(app)
    scheduler.debounce_seconds = 0.05
    title = f"Auto Extract {uuid.uuid4()}"
    payload = {
        "client_id": "auto-client",
        "messages": [
            {
                "chat_title": title,
                "text": "Selling Pixel 7 Pro - $610 shipped",
                "observed_at": datetime.utcnow().isoformat() + "Z",
            }
        ],
    }
    response = client.post(
        "/integrations/whatsapp/ingest",
        json=payload,
        headers={"x-ingest-token": "test-token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created"] >= 1

    deadline = time.time() + 1.5
    chat_ready = False
    while time.time() < deadline:
        with get_session() as session:
            chat = session.exec(select(models.WhatsAppChat).where(models.WhatsAppChat.title == title)).first()
            if chat and chat.last_extracted_at is not None:
                offers = session.exec(select(models.Offer)).all()
                assert any(o.source_whatsapp_message_id is not None for o in offers)
                chat_ready = True
                break
        time.sleep(0.1)

    assert chat_ready is True

    with get_session() as session:
        chat = session.exec(select(models.WhatsAppChat).where(models.WhatsAppChat.title == title)).first()
        assert chat is not None
        assert chat.last_extracted_at is not None

    snapshot = metrics.snapshot()
    assert any(entry.extracted >= 1 for entry in snapshot if entry.chat_title == title)


def test_diagnostics_exposes_whatsapp_metrics():
    client = TestClient(app)
    title = f"Diagnostics {uuid.uuid4()}"
    payload = {
        "client_id": "diag-client",
        "messages": [
            {
                "chat_title": title,
                "text": "Samsung S24 Ultra - $980",
            }
        ],
    }
    response = client.post(
        "/integrations/whatsapp/ingest",
        json=payload,
        headers={"x-ingest-token": "test-token"},
    )
    assert response.status_code == 200

    diagnostics = client.get("/chat/tools/diagnostics")
    assert diagnostics.status_code == 200
    payload = diagnostics.json()
    whatsapp_metrics = payload.get("whatsapp_metrics", [])
    assert whatsapp_metrics
    assert any(entry["client_id"] == "diag-client" for entry in whatsapp_metrics)
