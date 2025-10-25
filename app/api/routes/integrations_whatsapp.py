from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session

from app.api.deps import get_db
from app.db.session import init_db
from app.core.config import settings
from app.services.whatsapp_ingest import WhatsAppIngestService
from app.services.whatsapp_extract import WhatsAppExtractionService
from app.db import models
from sqlmodel import select


router = APIRouter(prefix="/integrations/whatsapp", tags=["integrations-whatsapp"])


class WhatsAppMessageIn(BaseModel):
    chat_title: str = Field(..., min_length=1, max_length=200)
    text: str = Field(..., min_length=1)
    observed_at: datetime | None = None
    message_id: str | None = None
    sender_name: str | None = None
    sender_phone: str | None = None
    is_outgoing: bool | None = None
    chat_type: str | None = None
    platform_id: str | None = None
    raw_payload: dict[str, Any] | None = None

    @field_validator("chat_title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        return value.strip()

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()


class WhatsAppIngestBatch(BaseModel):
    client_id: str = Field(..., min_length=3, max_length=200)
    messages: list[WhatsAppMessageIn]


@router.post("/ingest", summary="Ingest WhatsApp Web messages (raw)")
def ingest(
    payload: WhatsAppIngestBatch,
    db: Session = Depends(get_db),
    x_ingest_token: str | None = Header(default=None, alias="x-ingest-token"),
) -> dict[str, int]:
    # Defensive: ensure tables exist (helps local/dev and test envs)
    try:
        init_db()
    except Exception:
        # non-fatal; proceed and let DB layer raise if needed
        pass
    # Simple shared-secret gate if configured
    required = settings.whatsapp_ingest_token
    env = settings.environment.lower()
    if env in {"prod", "production"}:
        if not required:
            raise HTTPException(status_code=503, detail="ingest disabled: missing token config")
        if x_ingest_token != required:
            raise HTTPException(status_code=401, detail="invalid ingest token")
    elif required and x_ingest_token != required:
        # In non-prod, allow open ingest unless a token is configured
        raise HTTPException(status_code=401, detail="invalid ingest token")

    # Pre-scan existing keys for this batch to measure new inserts accurately
    def _content_hash(text: str, sender: str | None, chat_title: str) -> str:
        import hashlib

        base = f"{chat_title}\n{sender or ''}\n{text.strip()}".encode("utf-8", errors="ignore")
        return hashlib.sha256(base).hexdigest()

    def _exists(title: str, message_id: str | None, content_hash: str | None) -> bool:
        chat = db.exec(select(models.WhatsAppChat).where(models.WhatsAppChat.title == title)).first()
        if not chat:
            return False
        stmt = select(models.WhatsAppMessage).where(models.WhatsAppMessage.chat_id == chat.id)
        if message_id:
            stmt = stmt.where(models.WhatsAppMessage.message_id == message_id)
        elif content_hash:
            stmt = stmt.where(models.WhatsAppMessage.content_hash == content_hash)
        else:
            return False
        return db.exec(stmt).first() is not None

    pre_existing: set[tuple[str, str]] = set()
    for m in payload.messages:
        title = m.chat_title
        key = m.message_id or _content_hash(m.text, m.sender_name, title)
        if _exists(title, m.message_id, None if m.message_id else key):
            pre_existing.add((title, key))

    service = WhatsAppIngestService(db)
    result = service.ingest_messages(client_id=payload.client_id, items=[m.model_dump() for m in payload.messages])
    # Persist before computing new rows
    try:
        db.commit()
    except Exception:
        pass

    # Measure post-existing and compute newly created keys
    post_existing: set[tuple[str, str]] = set()
    for m in payload.messages:
        title = m.chat_title
        key = m.message_id or _content_hash(m.text, m.sender_name, title)
        if _exists(title, m.message_id, None if m.message_id else key):
            post_existing.add((title, key))

    created_count = len(post_existing - pre_existing)

    accepted = len(payload.messages)
    # Prefer authoritative created/deduped counts computed above when they differ
    if created_count != result.get("created", created_count):
        result = {**result, "created": created_count, "deduped": max(0, accepted - created_count)}
    return {"accepted": accepted, **result}


class ChatSummary(BaseModel):
    id: str
    title: str
    last_message_at: datetime | None = None
    message_count: int


@router.get("/chats", response_model=list[ChatSummary], summary="List WhatsApp chats")
def list_chats(db: Session = Depends(get_db)) -> list[ChatSummary]:
    chats = db.exec(select(models.WhatsAppChat)).all()
    out: list[ChatSummary] = []
    for chat in chats:
        last = db.exec(
            select(models.WhatsAppMessage)
            .where(models.WhatsAppMessage.chat_id == chat.id)
            .order_by(models.WhatsAppMessage.observed_at.desc())
            .limit(1)
        ).first()
        count = db.exec(select(models.WhatsAppMessage).where(models.WhatsAppMessage.chat_id == chat.id)).count()
        out.append(
            ChatSummary(id=str(chat.id), title=chat.title, last_message_at=last.observed_at if last else None, message_count=count)
        )
    return out


class ExtractResponse(BaseModel):
    offers: int
    warnings: int
    document_id: str | None = None


@router.post("/chats/{chat_id}/extract", response_model=ExtractResponse, summary="Extract deals from a chat")
def extract_chat(chat_id: UUID, db: Session = Depends(get_db)) -> ExtractResponse:
    chat = db.get(models.WhatsAppChat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="chat not found")
    svc = WhatsAppExtractionService(db)
    result = svc.extract_from_chat(chat)
    return ExtractResponse(**result)


@router.post("/chats/{chat_id}/extract-latest", response_model=ExtractResponse, summary="Extract new deals since last run")
def extract_chat_latest(chat_id: UUID, db: Session = Depends(get_db)) -> ExtractResponse:
    chat = db.get(models.WhatsAppChat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="chat not found")
    svc = WhatsAppExtractionService(db)
    result = svc.extract_from_chat(chat, since=chat.last_extracted_at)
    return ExtractResponse(**result)
