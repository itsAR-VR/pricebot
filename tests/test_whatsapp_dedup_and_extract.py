from datetime import datetime
import uuid

from fastapi.testclient import TestClient
from sqlmodel import select

from app.main import app
from app.db.session import get_session
from app.db import models
from sqlmodel import select


def test_dedup_by_message_id_within_batch_and_across_calls():
    client = TestClient(app)

    title = f"Deals Group {uuid.uuid4()}"
    payload = {
        "client_id": "dev-client",
        "messages": [
            {
                "chat_title": title,
                "text": "WTB 100 Laptops $70 each",
                "message_id": "abc-123",
                "observed_at": datetime.utcnow().isoformat() + "Z",
                "sender_name": "Ali",
            },
            {
                "chat_title": title,
                "text": "WTB 100 Laptops $70 each",
                "message_id": "abc-123",
                "observed_at": datetime.utcnow().isoformat() + "Z",
                "sender_name": "Ali",
            },
        ],
    }
    r1 = client.post(
        "/integrations/whatsapp/ingest",
        json=payload,
        headers={"x-ingest-token": "test-token"},
    )
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["accepted"] == 2
    assert data1["created"] == 1
    assert data1["deduped"] == 1
    statuses = {entry["status"] for entry in data1["decisions"]}
    assert statuses == {"created", "deduped"}

    # Repeat same payload; should dedup against DB
    r2 = client.post(
        "/integrations/whatsapp/ingest",
        json=payload,
        headers={"x-ingest-token": "test-token"},
    )
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["created"] == 0
    assert data2["deduped"] >= 1


def test_extract_creates_offers():
    client = TestClient(app)

    title2 = f"Deals Group {uuid.uuid4()}"
    payload = {
      "client_id": "dev-client",
      "messages": [
        {"chat_title": title2, "text": "Selling Pixel 8 128GB - $520 net", "sender_name": "Sara", "observed_at": datetime.utcnow().isoformat() + "Z"},
        {"chat_title": title2, "text": "Hello everyone"},
      ]
    }
    resp = client.post(
        "/integrations/whatsapp/ingest",
        json=payload,
        headers={"x-ingest-token": "test-token"},
    )
    assert resp.status_code == 200

    with get_session() as session:
        chat = session.exec(select(models.WhatsAppChat).where(models.WhatsAppChat.title == title2)).first()
        assert chat is not None
        extract = client.post(f"/integrations/whatsapp/chats/{chat.id}/extract")
        assert extract.status_code == 200
        data = extract.json()
        assert data["offers"] >= 1

        # Extract latest should not duplicate for same messages
        extract2 = client.post(f"/integrations/whatsapp/chats/{chat.id}/extract-latest")
        assert extract2.status_code == 200
        data2 = extract2.json()
        assert data2["offers"] == 0


def test_extract_uses_vendor_mapping():
    client = TestClient(app)
    vendor_name = f"Mapped Vendor {uuid.uuid4()}"
    chat_title = f"Mapped Chat {uuid.uuid4()}"
    with get_session() as session:
        vendor = models.Vendor(name=vendor_name)
        chat = models.WhatsAppChat(title=chat_title, vendor=vendor)
        session.add(vendor)
        session.add(chat)
        session.commit()
        chat_id = chat.id
        vendor_id = vendor.id

    payload = {
        "client_id": "dev-client",
        "messages": [
            {
                "chat_title": chat_title,
                "text": "Selling Surface Laptop 5 - $850 firm",
                "sender_name": "Chris",
            }
        ],
    }
    ingest = client.post(
        "/integrations/whatsapp/ingest",
        json=payload,
        headers={"x-ingest-token": "test-token"},
    )
    assert ingest.status_code == 200

    extract = client.post(f"/integrations/whatsapp/chats/{chat_id}/extract")
    assert extract.status_code == 200
    data = extract.json()
    assert data["offers"] >= 1

    document_id = uuid.UUID(data["document_id"])
    with get_session() as session:
        offers = session.exec(select(models.Offer).where(models.Offer.source_document_id == document_id)).all()
        assert offers, "Expected persisted offers for mapped chat"
        for offer in offers:
            assert offer.vendor_id == vendor_id
        doc = session.exec(select(models.SourceDocument).where(models.SourceDocument.id == document_id)).first()
        assert doc is not None
        assert doc.vendor_id == vendor_id
