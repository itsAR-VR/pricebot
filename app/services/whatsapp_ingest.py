from __future__ import annotations

import hashlib
from datetime import datetime, timezone, timedelta
from typing import Iterable
from uuid import UUID

from sqlmodel import Session, select

from app.db import models
from app.core.config import settings


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
        decisions: list[dict] = []
        chats_with_new_messages: set[str] = set()

        chat_cache: dict[str, models.WhatsAppChat] = {}
        seen_ids: set[tuple[str, str]] = set()
        seen_hashes: set[tuple[str, str]] = set()
        window_hours = max(0, settings.whatsapp_content_hash_window_hours)
        window_delta = timedelta(hours=window_hours) if window_hours else None

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
            raw_media = item.get("media")
            media_info = dict(raw_media) if raw_media else None
            media_document_id = None
            if media_info:
                media_document_id = media_info.get("document_id")
                if media_document_id:
                    media_document_id = str(media_document_id)
                    media_info["document_id"] = media_document_id
                if not text:
                    text = media_info.get("caption")
                if not text:
                    fallback = media_info.get("kind") or media_info.get("mimetype") or "media"
                    fallback = str(fallback).split("/", 1)[0]
                    text = f"[{fallback}]"

            if not text or not text.strip():
                decisions.append(
                    {
                        "chat_title": chat_title,
                        "platform_id": platform_id,
                        "message_id": message_id,
                        "status": "skipped",
                        "reason": "empty_text",
                    }
                )
                continue
            text = text.strip()

            chat = chat_cache.get(chat_title)
            if not chat:
                existing = self.session.exec(
                    select(models.WhatsAppChat).where(models.WhatsAppChat.title == chat_title)
                ).first()
                if existing is None:
                    chat = self.ensure_chat(chat_title, chat_type=chat_type, platform_id=platform_id)
                    created_chats += 1
                else:
                    chat = existing
                    if chat_type and chat.chat_type != chat_type:
                        chat.chat_type = chat_type
                    if platform_id and chat.platform_id != platform_id:
                        chat.platform_id = platform_id
                chat_cache[chat_title] = chat

            decision = {
                "chat_id": str(chat.id),
                "chat_title": chat_title,
                "platform_id": platform_id or chat.platform_id,
                "message_id": message_id,
            }
            if media_info and media_info.get("document_id"):
                decision["media_document_id"] = str(media_info["document_id"])

            content_hash = _content_hash(text, sender=sender_name, chat_title=chat_title)
            decision["content_hash"] = content_hash

            # Prefer strict dedup on (chat_id, message_id) if provided
            if message_id:
                stmt_by_id = (
                    select(models.WhatsAppMessage)
                    .where(models.WhatsAppMessage.chat_id == chat.id)
                    .where(models.WhatsAppMessage.message_id == message_id)
                )
                if (str(chat.id), message_id) in seen_ids or self.session.exec(stmt_by_id).first() is not None:
                    deduped += 1
                    decisions.append({**decision, "status": "deduped", "reason": "duplicate_message_id"})
                    continue
                seen_ids.add((str(chat.id), message_id))

            # Dedup within recent window (24h) by content hash + sender
            if window_delta:
                window_start = _utcnow() - window_delta
                stmt = (
                    select(models.WhatsAppMessage)
                    .where(models.WhatsAppMessage.chat_id == chat.id)
                    .where(models.WhatsAppMessage.content_hash == content_hash)
                    .where(models.WhatsAppMessage.observed_at >= window_start)
                )
                if ((str(chat.id), content_hash) in seen_hashes) or (self.session.exec(stmt).first() is not None):
                    deduped += 1
                    decisions.append(
                        {**decision, "status": "deduped", "reason": "duplicate_content_hash_within_window"}
                    )
                    continue
                seen_hashes.add((str(chat.id), content_hash))

            payload_dict: dict | None = None
            if raw_payload:
                payload_dict = dict(raw_payload)
            if media_info:
                if payload_dict is None:
                    payload_dict = {}
                payload_dict["media"] = media_info

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
                raw_payload=payload_dict,
            )
            self.session.add(msg)
            self.session.flush()

            if media_document_id:
                self._link_media_document(media_document_id, msg, message_id=message_id, chat=chat)

            created += 1
            chats_with_new_messages.add(str(chat.id))
            decisions.append(
                {
                    **decision,
                    "status": "created",
                    "whatsapp_message_id": str(msg.id),
                }
            )

        return {
            "created": created,
            "deduped": deduped,
            "created_chats": created_chats,
            "decisions": decisions,
            "chats_with_new_messages": list(chats_with_new_messages),
        }

    def _link_media_document(
        self,
        document_id: str | None,
        message: models.WhatsAppMessage,
        *,
        message_id: str | None,
        chat: models.WhatsAppChat,
    ) -> None:
        if not document_id:
            return
        try:
            doc_uuid = UUID(str(document_id))
        except (TypeError, ValueError):
            return

        document = self.session.get(models.SourceDocument, doc_uuid)
        if not document:
            return

        if document.source_whatsapp_message_id and document.source_whatsapp_message_id != message.id:
            return

        document.source_whatsapp_message_id = message.id
        extra = dict(document.extra or {})
        extra.setdefault("chat_id", str(chat.id))
        extra.setdefault("chat_title", chat.title)
        if message_id:
            extra.setdefault("message_id", message_id)
        extra.setdefault("source", "whatsapp_media")
        document.extra = extra
        self.session.add(document)
