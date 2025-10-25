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
    r1 = client.post("/integrations/whatsapp/ingest", json=payload)
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["accepted"] == 2
    assert data1["created"] == 1
    assert data1["deduped"] == 1

    # Repeat same payload; should dedup against DB
    r2 = client.post("/integrations/whatsapp/ingest", json=payload)
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
    resp = client.post("/integrations/whatsapp/ingest", json=payload)
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
