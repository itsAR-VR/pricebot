from __future__ import annotations

import hashlib
from datetime import datetime, timezone, timedelta
from typing import Iterable

from sqlmodel import Session, select

from app.db import models


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _content_hash(text: str, *, sender: str | None, chat_title: str) -> str:
    base = f"{chat_title}\n{sender or ''}\n{text.strip()}".encode("utf-8", errors="ignore")
    return hashlib.sha256(base).hexdigest()


class WhatsAppIngestService:
    """Persist raw WhatsApp chat messages for later extraction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_chat(self, title: str, *, chat_type: str | None = None, platform_id: str | None = None) -> models.WhatsAppChat:
        stmt = select(models.WhatsAppChat).where(models.WhatsAppChat.title == title)
        existing = self.session.exec(stmt).first()
        if existing:
            return existing
        chat = models.WhatsAppChat(title=title, chat_type=chat_type, platform_id=platform_id)
        self.session.add(chat)
        self.session.flush()
        return chat

    def ingest_messages(self, *, client_id: str | None, items: Iterable[dict]) -> dict:
        created = 0
        deduped = 0
        created_chats = 0

        chat_cache: dict[str, models.WhatsAppChat] = {}
        seen_ids: set[tuple[str, str]] = set()
        seen_hashes: set[tuple[str, str]] = set()

        for item in items:
            chat_title: str = (item.get("chat_title") or "Unknown Chat").strip()
            chat_type = item.get("chat_type")
            platform_id = item.get("platform_id")
            sender_name = item.get("sender_name")
            sender_phone = item.get("sender_phone")
            is_outgoing = item.get("is_outgoing")
            message_id = item.get("message_id")
            text: str | None = item.get("text")
            observed_at = item.get("observed_at")
            raw_payload = item.get("raw_payload")

            if not text or not text.strip():
                continue

            chat = chat_cache.get(chat_title)
            if not chat:
                pre_count = self.session.exec(select(models.WhatsAppChat).where(models.WhatsAppChat.title == chat_title)).first()
                if pre_count is None:
                    chat = self.ensure_chat(chat_title, chat_type=chat_type, platform_id=platform_id)
                    created_chats += 1
                else:
                    chat = pre_count
                chat_cache[chat_title] = chat

            content_hash = _content_hash(text, sender=sender_name, chat_title=chat_title)

            # Prefer strict dedup on (chat_id, message_id) if provided
            if message_id:
                stmt_by_id = (
                    select(models.WhatsAppMessage)
                    .where(models.WhatsAppMessage.chat_id == chat.id)
                    .where(models.WhatsAppMessage.message_id == message_id)
                )
                if (str(chat.id), message_id) in seen_ids or self.session.exec(stmt_by_id).first() is not None:
                    deduped += 1
                    continue
                seen_ids.add((str(chat.id), message_id))

            # Dedup within recent window (24h) by content hash + sender
            window_start = (_utcnow() - timedelta(days=1))
            stmt = (
                select(models.WhatsAppMessage)
                .where(models.WhatsAppMessage.chat_id == chat.id)
                .where(models.WhatsAppMessage.content_hash == content_hash)
                .where(models.WhatsAppMessage.observed_at >= window_start)
            )
            if ((str(chat.id), content_hash) in seen_hashes) or (self.session.exec(stmt).first() is not None):
                deduped += 1
                continue
            seen_hashes.add((str(chat.id), content_hash))

            msg = models.WhatsAppMessage(
                chat_id=chat.id,
                client_id=client_id,
                observed_at=observed_at or _utcnow(),
                sender_name=sender_name,
                sender_phone=sender_phone,
                is_outgoing=is_outgoing,
                message_id=message_id,
                text=text,
                content_hash=content_hash,
                raw_payload=raw_payload,
            )
            self.session.add(msg)
            created += 1

        return {"created": created, "deduped": deduped, "created_chats": created_chats}
