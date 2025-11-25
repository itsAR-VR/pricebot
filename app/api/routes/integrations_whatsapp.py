from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlmodel import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.deps import get_db
from app.db.session import init_db
from app.core.config import settings
from app.core.metrics import metrics
from app.core.rate_limit import TokenBucketLimiter
from app.services.whatsapp_ingest import WhatsAppIngestService
from app.services.whatsapp_extract import WhatsAppExtractionService
from app.services.whatsapp_outbound import WhatsAppOutboundService
from app.services.whatsapp_scheduler import scheduler
from app.db import models
from sqlmodel import select
from app.services.ingestion_jobs import ingestion_job_runner
from app.services.media_storage import (
    MediaStorageError,
    get_media_storage,
    sanitize_filename,
)


router = APIRouter(prefix="/integrations/whatsapp", tags=["integrations-whatsapp"])
logger = logging.getLogger("pricebot.api.whatsapp")

SUPPORTED_MEDIA_MIME_PREFIXES: tuple[str, ...] = ("image/", "video/", "audio/")
SUPPORTED_MEDIA_MIME_TYPES: set[str] = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _record_http_failure(
    *,
    client_id: str | None,
    status_code: int,
    reason: str,
    chat_id: str | None = None,
    chat_title: str | None = None,
) -> None:
    metrics.record_http_event(
        client_id=client_id,
        chat_id=chat_id,
        chat_title=chat_title,
        status_code=status_code,
        reason=reason,
    )


def _require_ingest_token(provided: str | None, *, client_id: str | None = None) -> None:
    required = settings.whatsapp_ingest_token
    if not required:
        _record_http_failure(client_id=client_id, status_code=503, reason="ingest_disabled")
        raise HTTPException(status_code=503, detail="ingest disabled: missing token config")
    if provided != required:
        _record_http_failure(client_id=client_id, status_code=401, reason="invalid_ingest_token")
        raise HTTPException(status_code=401, detail="invalid ingest token")


def _parse_signature_timestamp(raw: str) -> datetime:
    normalized = raw.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _validate_signature(
    body: bytes,
    signature: str | None,
    timestamp: str | None,
    *,
    client_id: str | None = None,
) -> None:
    secret = settings.whatsapp_ingest_hmac_secret
    if not secret:
        return
    if not signature or not timestamp:
        _record_http_failure(
            client_id=client_id,
            status_code=401,
            reason="missing_signature_headers",
        )
        raise HTTPException(status_code=401, detail="missing signature headers")
    try:
        ts = _parse_signature_timestamp(timestamp)
    except ValueError as exc:  # pragma: no cover - defensive
        _record_http_failure(
            client_id=client_id,
            status_code=400,
            reason="invalid_signature_timestamp",
        )
        raise HTTPException(status_code=400, detail="invalid signature timestamp") from exc
    ttl = max(0, settings.whatsapp_ingest_signature_ttl_seconds)
    if ttl and abs((_utcnow() - ts).total_seconds()) > ttl:
        _record_http_failure(
            client_id=client_id,
            status_code=403,
            reason="stale_signature",
        )
        raise HTTPException(status_code=403, detail="stale signature")
    message = timestamp.encode("utf-8") + b"." + body
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.lower(), expected.lower()):
        _record_http_failure(
            client_id=client_id,
            status_code=403,
            reason="invalid_signature",
        )
        raise HTTPException(status_code=403, detail="invalid signature")


def _create_rate_limiter() -> TokenBucketLimiter | None:
    per_minute = settings.whatsapp_ingest_rate_limit_per_minute
    burst = settings.whatsapp_ingest_rate_limit_burst
    if per_minute <= 0 or burst <= 0:
        return None
    try:
        return TokenBucketLimiter(capacity=float(burst), refill_rate=float(per_minute) / 60.0)
    except ValueError:  # pragma: no cover - invalid configuration
        return None


_rate_limiter = _create_rate_limiter()


def _enforce_rate_limit(
    client_id: str | None,
    weight: int,
    *,
    chat_id: str | None = None,
    chat_title: str | None = None,
) -> None:
    limiter = _rate_limiter
    if limiter is None:
        return
    tokens = max(1.0, float(weight))
    if limiter.allow(client_id or "unknown", tokens):
        return
    _record_http_failure(
        client_id=client_id,
        chat_id=chat_id,
        chat_title=chat_title,
        status_code=429,
        reason="rate_limited",
    )
    raise HTTPException(
        status_code=429,
        detail="ingest rate limit exceeded",
        headers={"Retry-After": "1"},
    )


def _normalize_media_kind(mimetype: str | None, declared: str | None) -> str | None:
    if declared:
        return declared
    if not mimetype:
        return None
    if mimetype.startswith("image/"):
        return "image"
    if mimetype.startswith("video/"):
        return "video"
    if mimetype in {"application/pdf"}:
        return "document"
    return mimetype.split("/", 1)[0]


def _is_supported_media(mimetype: str | None) -> bool:
    if not mimetype:
        return False
    mimetype_lower = mimetype.lower()
    for prefix in SUPPORTED_MEDIA_MIME_PREFIXES:
        if mimetype_lower.startswith(prefix):
            return True
    return mimetype_lower in SUPPORTED_MEDIA_MIME_TYPES


def _parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid integer value") from exc


class WhatsAppIngestDecision(BaseModel):
    chat_id: str | None = None
    chat_title: str | None = None
    platform_id: str | None = None
    message_id: str | None = None
    whatsapp_message_id: str | None = None
    content_hash: str | None = None
    status: Literal["created", "deduped", "skipped"]
    reason: str | None = None
    media_document_id: str | None = None


class WhatsAppIngestResponse(BaseModel):
    request_id: str
    accepted: int
    created: int
    deduped: int
    created_chats: int
    decisions: list[WhatsAppIngestDecision] = Field(default_factory=list)


class WhatsAppMediaMetadata(BaseModel):
    mimetype: str = Field(..., min_length=1, max_length=200)
    filename: str | None = Field(default=None, max_length=255)
    size_bytes: int | None = Field(default=None, ge=0)
    caption: str | None = None
    kind: str | None = Field(default=None, max_length=40)
    document_id: UUID | None = None
    failure_reason: str | None = Field(default=None, max_length=100)


class WhatsAppMessageIn(BaseModel):
    chat_title: str = Field(..., min_length=1, max_length=200)
    text: str | None = Field(default=None)
    observed_at: datetime | None = None
    message_id: str | None = None
    sender_name: str | None = None
    sender_phone: str | None = None
    is_outgoing: bool | None = None
    chat_type: str | None = None
    platform_id: str | None = None
    raw_payload: dict[str, Any] | None = None
    media: WhatsAppMediaMetadata | None = None

    @field_validator("chat_title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        return value.strip()

    @field_validator("text", mode="before")
    @classmethod
    def _normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def _require_text_or_media(self) -> "WhatsAppMessageIn":
        # Allow empty messages through so downstream dedupe logic can flag them as skipped.
        return self


class WhatsAppIngestBatch(BaseModel):
    client_id: str = Field(..., min_length=3, max_length=200)
    messages: list[WhatsAppMessageIn]

    @field_validator("messages")
    @classmethod
    def _validate_messages(cls, value: list[WhatsAppMessageIn]) -> list[WhatsAppMessageIn]:
        if not value:
            raise ValueError("messages must contain at least one item")
        return value


class WhatsAppMediaUploadResponse(BaseModel):
    request_id: str
    status: Literal["queued", "deduped"]
    document_id: str | None = None
    job_id: str | None = None
    size_bytes: int | None = None
    media_sha256: str | None = None
    dedup_reason: str | None = None


def _find_whatsapp_message(
    session: Session,
    *,
    message_id: str | None,
    client_id: str | None,
    chat_title: str | None,
    platform_id: str | None,
) -> models.WhatsAppMessage | None:
    if not message_id:
        return None

    stmt = select(models.WhatsAppMessage)
    needs_join = bool(chat_title or platform_id)
    if needs_join:
        stmt = stmt.join(models.WhatsAppChat)

    stmt = stmt.where(models.WhatsAppMessage.message_id == message_id)
    if client_id:
        stmt = stmt.where(models.WhatsAppMessage.client_id == client_id)
    if chat_title:
        stmt = stmt.where(models.WhatsAppChat.title == chat_title)
    if platform_id:
        stmt = stmt.where(models.WhatsAppChat.platform_id == platform_id)

    stmt = stmt.limit(1)
    return session.exec(stmt).first()


def _find_existing_media_document(
    session: Session,
    *,
    message_uuid: UUID | None,
    message_id: str | None,
    media_hash: str | None,
) -> models.SourceDocument | None:
    if message_uuid:
        existing = session.exec(
            select(models.SourceDocument).where(
                models.SourceDocument.source_whatsapp_message_id == message_uuid
            )
        ).first()
        if existing:
            return existing

    stmt = select(models.SourceDocument).where(models.SourceDocument.file_type == "whatsapp_media")
    for candidate in session.exec(stmt):
        extra = candidate.extra or {}
        if media_hash and extra.get("media_sha256") == media_hash:
            return candidate
        if message_id and extra.get("message_id") == message_id:
            return candidate
    return None


@router.post(
    "/ingest",
    summary="Ingest WhatsApp Web messages (raw)",
    response_model=WhatsAppIngestResponse,
)
async def ingest(
    request: Request,
    payload: WhatsAppIngestBatch,
    db: Session = Depends(get_db),
    x_ingest_token: str | None = Header(default=None, alias="x-ingest-token"),
    x_signature: str | None = Header(default=None, alias="x-signature"),
    x_signature_timestamp: str | None = Header(default=None, alias="x-signature-timestamp"),
) -> WhatsAppIngestResponse:
    request_id = str(uuid4())
    try:
        init_db()
    except Exception:
        pass

    _require_ingest_token(x_ingest_token, client_id=payload.client_id)
    body_bytes = await request.body()
    _validate_signature(
        body_bytes,
        x_signature,
        x_signature_timestamp,
        client_id=payload.client_id,
    )

    accepted = len(payload.messages)
    _enforce_rate_limit(payload.client_id, max(1, accepted))

    service = WhatsAppIngestService(db)
    result = service.ingest_messages(client_id=payload.client_id, items=[m.model_dump() for m in payload.messages])

    try:
        db.commit()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception(
            "WhatsApp ingest commit failed",
            extra={"request_id": request_id, "client_id": payload.client_id, "error": str(exc)},
        )
        raise

    decisions_data = result.get("decisions", [])
    metrics.record_ingest(client_id=payload.client_id, decisions=decisions_data)

    created = int(result.get("created", 0))
    deduped = int(result.get("deduped", 0))
    created_chats = int(result.get("created_chats", 0))

    if created and result.get("chats_with_new_messages"):
        for chat_id in result["chats_with_new_messages"]:
            try:
                scheduler.schedule(UUID(chat_id), client_id=payload.client_id)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception(
                    "Failed to schedule WhatsApp extract",
                    extra={"request_id": request_id, "chat_id": chat_id, "error": str(exc)},
                )

    logger.info(
        "WhatsApp ingest processed",
        extra={
            "request_id": request_id,
            "client_id": payload.client_id,
            "ingest_accepted": accepted,
            "ingest_created": created,
            "ingest_deduped": deduped,
            "ingest_created_chats": created_chats,
        },
    )

    decisions = [WhatsAppIngestDecision(**entry) for entry in decisions_data]
    return WhatsAppIngestResponse(
        request_id=request_id,
        accepted=accepted,
        created=created,
        deduped=deduped,
        created_chats=created_chats,
        decisions=decisions,
    )


@router.post(
    "/media",
    summary="Upload WhatsApp media attachment",
    response_model=WhatsAppMediaUploadResponse,
)
async def upload_media(
    request: Request,
    db: Session = Depends(get_db),
    x_ingest_token: str | None = Header(default=None, alias="x-ingest-token"),
    x_signature: str | None = Header(default=None, alias="x-signature"),
    x_signature_timestamp: str | None = Header(default=None, alias="x-signature-timestamp"),
) -> WhatsAppMediaUploadResponse:
    request_id = str(uuid4())
    try:
        init_db()
    except Exception:
        pass

    _require_ingest_token(x_ingest_token)
    body_bytes = await request.body()
    _validate_signature(body_bytes, x_signature, x_signature_timestamp)

    form = await request.form()
    client_id = (form.get("client_id") or "").strip()
    chat_title = (form.get("chat_title") or "").strip()
    upload = form.get("file")
    if not isinstance(upload, StarletteUploadFile):
        _record_http_failure(
            client_id=client_id or None,
            chat_title=chat_title or None,
            status_code=400,
            reason="file_upload_missing",
        )
        raise HTTPException(status_code=400, detail="file upload is required")

    if not client_id:
        _record_http_failure(
            client_id=None,
            chat_title=chat_title or None,
            status_code=400,
            reason="missing_client_id",
        )
        raise HTTPException(status_code=400, detail="client_id is required")
    if not chat_title:
        _record_http_failure(
            client_id=client_id or None,
            status_code=400,
            reason="missing_chat_title",
        )
        raise HTTPException(status_code=400, detail="chat_title is required")

    message_id = form.get("message_id") or None
    chat_platform_id = form.get("chat_platform_id") or form.get("platform_id") or None
    media_kind_declared = form.get("media_kind") or form.get("kind")
    caption = form.get("caption") or None
    mimetype = form.get("mimetype") or upload.content_type or "application/octet-stream"
    declared_size = _parse_int(form.get("size_bytes"))
    observed_at = form.get("observed_at") or None

    content = await upload.read()
    if not content:
        _record_http_failure(
            client_id=client_id or None,
            chat_title=chat_title or None,
            status_code=400,
            reason="empty_media_payload",
        )
        raise HTTPException(status_code=400, detail="empty media payload")

    actual_size = len(content)
    size_bytes = declared_size if declared_size is not None else actual_size

    if actual_size > settings.whatsapp_media_max_bytes:
        metrics.record_media_upload(
            client_id=client_id,
            chat_id=None,
            chat_title=chat_title,
            status="failed",
            reason="too_large",
        )
        _record_http_failure(
            client_id=client_id or None,
            chat_title=chat_title or None,
            status_code=413,
            reason="media_too_large",
        )
        raise HTTPException(status_code=413, detail="media exceeds maximum size")

    if not _is_supported_media(mimetype):
        metrics.record_media_upload(
            client_id=client_id,
            chat_id=None,
            chat_title=chat_title,
            status="failed",
            reason="unsupported_type",
        )
        _record_http_failure(
            client_id=client_id or None,
            chat_title=chat_title or None,
            status_code=415,
            reason="media_unsupported",
        )
        raise HTTPException(status_code=415, detail="unsupported media type")

    media_hash = hashlib.sha256(content).hexdigest()
    media_kind = _normalize_media_kind(mimetype, media_kind_declared)

    rate_weight = max(1, actual_size // (256 * 1024) + 1)
    _enforce_rate_limit(
        client_id,
        rate_weight,
        chat_title=chat_title or None,
    )

    message = _find_whatsapp_message(
        db,
        message_id=message_id,
        client_id=client_id,
        chat_title=chat_title or None,
        platform_id=chat_platform_id,
    )
    message_uuid = message.id if message else None

    existing = _find_existing_media_document(
        db,
        message_uuid=message_uuid,
        message_id=message_id,
        media_hash=media_hash,
    )
    if existing:
        if message_uuid and existing.source_whatsapp_message_id != message_uuid:
            existing.source_whatsapp_message_id = message_uuid
            extra = dict(existing.extra or {})
            if message_id and not extra.get("message_id"):
                extra["message_id"] = message_id
            if message and not extra.get("chat_id"):
                extra["chat_id"] = str(message.chat_id)
            existing.extra = extra
            db.add(existing)
            try:
                db.commit()
            except Exception:  # pragma: no cover - defensive
                db.rollback()
        metrics.record_media_upload(
            client_id=client_id,
            chat_id=str(message.chat_id) if message else (existing.extra or {}).get("chat_id"),
            chat_title=chat_title or (existing.extra or {}).get("chat_title"),
            status="deduped",
            reason="duplicate",
        )
        return WhatsAppMediaUploadResponse(
            request_id=request_id,
            status="deduped",
            document_id=str(existing.id),
            job_id=None,
            size_bytes=(existing.extra or {}).get("size_bytes"),
            media_sha256=(existing.extra or {}).get("media_sha256") or media_hash,
            dedup_reason="duplicate",
        )

    original_name = upload.filename or f"{media_kind or 'media'}.bin"
    safe_name = sanitize_filename(original_name)
    storage = get_media_storage()
    try:
        storage_result = storage.persist(
            content=content,
            content_hash=media_hash,
            mimetype=mimetype,
            original_name=safe_name,
        )
    except MediaStorageError as exc:
        logger.exception(
            "Failed to persist WhatsApp media via backend %s",
            getattr(storage, "backend_name", "unknown"),
            extra={"request_id": request_id},
        )
        _record_http_failure(
            client_id=client_id or None,
            chat_title=chat_title or None,
            chat_id=str(message.chat_id) if message else None,
            status_code=500,
            reason="media_persist_failure",
        )
        metrics.record_media_upload(
            client_id=client_id,
            chat_id=str(message.chat_id) if message else None,
            chat_title=chat_title,
            status="failed",
            reason="storage_error",
        )
        raise HTTPException(status_code=500, detail="failed to persist media payload") from exc

    metadata_extra: dict[str, Any] = {
        "source": "whatsapp_media",
        "client_id": client_id,
        "chat_title": chat_title,
        "chat_platform_id": chat_platform_id,
        "message_id": message_id,
        "media_kind": media_kind,
        "mimetype": mimetype,
        "media_sha256": media_hash,
        "size_bytes": actual_size,
        "declared_size_bytes": size_bytes if declared_size is not None else None,
        "caption": caption,
        "storage_backend": getattr(storage, "backend_name", "local"),
        "storage_filename": storage_result.storage_filename,
        "original_filename": original_name,
        "observed_at": observed_at,
        "media_fingerprint": f"{media_hash}:{size_bytes}",
    }
    metadata_extra.update(storage_result.extra)
    if message:
        metadata_extra["whatsapp_message_id"] = str(message.id)
        metadata_extra["chat_id"] = str(message.chat_id)

    document = models.SourceDocument(
        vendor_id=None,
        source_whatsapp_message_id=message_uuid,
        file_name=original_name,
        file_type="whatsapp_media",
        storage_path=storage_result.storage_path,
        status="queued",
        extra={k: v for k, v in metadata_extra.items() if v is not None},
    )
    db.add(document)
    db.flush()

    job_logs: dict[str, Any] = {
        "vendor_name": chat_title,
        "filename": original_name,
        "media_type": media_kind,
        "media_caption": caption,
        "client_id": client_id,
    }
    if message_uuid:
        job_logs["source_whatsapp_message_id"] = str(message_uuid)

    job = models.IngestionJob(
        source_document_id=document.id,
        processor="document_text",
        status="queued",
        logs={k: v for k, v in job_logs.items() if v is not None},
    )
    db.add(job)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to persist WhatsApp media metadata", extra={"request_id": request_id})
        metrics.record_media_upload(
            client_id=client_id,
            chat_id=str(message.chat_id) if message else (document.extra or {}).get("chat_id"),
            chat_title=chat_title,
            status="failed",
            reason="metadata_error",
        )
        _record_http_failure(
            client_id=client_id or None,
            chat_title=chat_title or None,
            status_code=500,
            reason="media_metadata_persist_failure",
        )
        raise HTTPException(status_code=500, detail="failed to persist media metadata") from exc

    db.refresh(document)
    db.refresh(job)
    ingestion_job_runner.enqueue(job.id)

    metrics.record_media_upload(
        client_id=client_id,
        chat_id=str(message.chat_id) if message else (document.extra or {}).get("chat_id"),
        chat_title=chat_title,
        status="queued",
    )

    return WhatsAppMediaUploadResponse(
        request_id=request_id,
        status="queued",
        document_id=str(document.id),
        job_id=str(job.id),
        size_bytes=actual_size,
        media_sha256=media_hash,
    )


class ChatSummary(BaseModel):
    id: str
    title: str
    last_message_at: datetime | None = None
    message_count: int
    vendor_id: UUID | None = None
    vendor_name: str | None = None


class ChatVendorMappingRequest(BaseModel):
    vendor_id: UUID | None = None


class ChatVendorMappingResponse(BaseModel):
    id: UUID
    vendor_id: UUID | None = None
    vendor_name: str | None = None


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
        count_result = db.exec(
            select(func.count())
            .select_from(models.WhatsAppMessage)
            .where(models.WhatsAppMessage.chat_id == chat.id)
        ).one()
        count = int(count_result[0] if isinstance(count_result, tuple) else count_result)
        vendor = chat.vendor
        out.append(
            ChatSummary(
                id=str(chat.id),
                title=chat.title,
                last_message_at=last.observed_at if last else None,
                message_count=count,
                vendor_id=chat.vendor_id,
                vendor_name=vendor.name if vendor else None,
            )
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
    response = ExtractResponse(**result)
    metrics.record_extract(
        client_id=None,
        chat_id=str(chat.id),
        chat_title=chat.title,
        offers=response.offers,
        errors=response.warnings,
    )
    return response


@router.post("/chats/{chat_id}/extract-latest", response_model=ExtractResponse, summary="Extract new deals since last run")
def extract_chat_latest(chat_id: UUID, db: Session = Depends(get_db)) -> ExtractResponse:
    chat = db.get(models.WhatsAppChat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="chat not found")
    svc = WhatsAppExtractionService(db)
    result = svc.extract_from_chat(chat, since=chat.last_extracted_at)
    response = ExtractResponse(**result)
    metrics.record_extract(
        client_id=None,
        chat_id=str(chat.id),
        chat_title=chat.title,
        offers=response.offers,
        errors=response.warnings,
    )
    return response


@router.put(
    "/chats/{chat_id}/vendor",
    response_model=ChatVendorMappingResponse,
    summary="Map a WhatsApp chat to a vendor",
)
def set_chat_vendor(
    chat_id: UUID,
    payload: ChatVendorMappingRequest,
    db: Session = Depends(get_db),
) -> ChatVendorMappingResponse:
    chat = db.get(models.WhatsAppChat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="chat not found")

    vendor = None
    if payload.vendor_id:
        vendor = db.get(models.Vendor, payload.vendor_id)
        if not vendor:
            raise HTTPException(status_code=404, detail="vendor not found")
        chat.vendor_id = vendor.id
    else:
        chat.vendor_id = None

    db.add(chat)
    db.commit()
    db.refresh(chat)
    vendor_name = vendor.name if vendor else None
    if not vendor_name and chat.vendor:
        vendor_name = chat.vendor.name
    return ChatVendorMappingResponse(id=chat.id, vendor_id=chat.vendor_id, vendor_name=vendor_name)


# ------------------------------------------------------------------
# Outbound Messaging
# ------------------------------------------------------------------


class SendMessageRequest(BaseModel):
    """Request payload for sending an outbound WhatsApp message."""

    text: str = Field(..., min_length=1, max_length=4000, description="Message text to send")


class SendMessageResponse(BaseModel):
    """Response from sending an outbound message."""

    success: bool
    message_id: str | None = None
    chat_title: str | None = None
    status: str | None = None
    error: str | None = None


@router.post(
    "/chats/{chat_id}/send",
    response_model=SendMessageResponse,
    summary="Send message to WhatsApp chat",
    description="Send an outbound message to a WhatsApp chat. "
    "Currently uses a mock relay that records the message to the database.",
)
def send_message(
    chat_id: UUID,
    payload: SendMessageRequest,
    db: Session = Depends(get_db),
) -> SendMessageResponse:
    """Send an outbound message to a WhatsApp chat.

    This endpoint:
    1. Validates the chat exists
    2. Records the message in the database with is_outgoing=True
    3. Attempts to send via relay (currently mocked)

    Returns success status and message details.
    """
    chat = db.get(models.WhatsAppChat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="chat not found")

    svc = WhatsAppOutboundService(db)
    result = svc.send_text(chat_id, payload.text)

    return SendMessageResponse(**result)
