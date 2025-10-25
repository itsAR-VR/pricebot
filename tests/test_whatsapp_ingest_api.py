from datetime import datetime
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_session
from app.db import models
from sqlmodel import select


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
    response = client.post("/integrations/whatsapp/ingest", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["accepted"] == 1
    assert data["created"] == 1

    # Verify persisted
    with get_session() as session:
        chat = session.exec(select(models.WhatsAppChat).where(models.WhatsAppChat.title == title)).first()
        assert chat is not None
        msgs = session.exec(select(models.WhatsAppMessage).where(models.WhatsAppMessage.chat_id == chat.id)).all()
        assert len(msgs) >= 1
