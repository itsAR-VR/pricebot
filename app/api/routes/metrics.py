from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.metrics import metrics

router = APIRouter(prefix="/metrics", tags=["metrics"])


def _serialize_counter(counter) -> dict[str, Any]:
    data: dict[str, Any] = {
        "client_id": counter.client_id,
        "chat_id": counter.chat_id,
        "chat_title": counter.chat_title,
        "accepted": counter.accepted,
        "created": counter.created,
        "deduped": counter.deduped,
        "extracted": counter.extracted,
        "errors": counter.errors,
        "media_uploaded": counter.media_uploaded,
        "media_deduped": counter.media_deduped,
        "media_failed": counter.media_failed,
        "http_4xx": counter.http_4xx,
        "http_5xx": counter.http_5xx,
        "auth_failures": counter.auth_failures,
        "forbidden": counter.forbidden,
        "rate_limited": counter.rate_limited,
        "signature_failures": counter.signature_failures,
        "last_event_at": counter.last_event_at.isoformat() if counter.last_event_at else None,
        "last_failure_status": counter.last_failure_status,
        "last_failure_reason": counter.last_failure_reason,
        "last_failure_at": counter.last_failure_at.isoformat() if counter.last_failure_at else None,
    }
    media_notes = getattr(counter, "media_notes", None)
    if media_notes:
        data["media_notes"] = list(media_notes)
    http_notes = getattr(counter, "http_notes", None)
    if http_notes:
        data["http_notes"] = list(http_notes)
    return data


def _serialize_recent_failures() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in metrics.recent_failures(limit=10):
        out.append(
            {
                "timestamp": event.timestamp.isoformat(),
                "client_id": event.client_id,
                "chat_id": event.chat_id,
                "chat_title": event.chat_title,
                "status_code": event.status_code,
                "reason": event.reason,
            }
        )
    return out


def _serialize_recent_media_failures() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in metrics.recent_media_failures(limit=10):
        out.append(
            {
                "timestamp": event.timestamp.isoformat(),
                "client_id": event.client_id,
                "chat_id": event.chat_id,
                "chat_title": event.chat_title,
                "reason": event.reason,
            }
        )
    return out


def _whatsapp_snapshot() -> dict[str, Any]:
    counters = metrics.snapshot()
    return {
        "totals": metrics.aggregate_totals(),
        "counters": [_serialize_counter(counter) for counter in counters],
        "recent_failures": _serialize_recent_failures(),
        "recent_media_failures": _serialize_recent_media_failures(),
        "generated_at": counters[0].last_event_at.isoformat() if counters else None,
    }


@router.get("", summary="Service metrics snapshot")
def metrics_root() -> dict[str, Any]:
    return {"whatsapp": _whatsapp_snapshot()}


@router.get("/whatsapp", summary="WhatsApp ingest metrics snapshot")
def metrics_whatsapp() -> dict[str, Any]:
    return _whatsapp_snapshot()
