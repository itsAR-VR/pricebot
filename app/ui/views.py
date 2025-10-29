from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from sqlalchemy import func

from app.api.deps import get_db
from app.api.routes.offers import OfferOut
from app.core.config import settings
from app.db import models

router = APIRouter(prefix="/admin", tags=["operator"], include_in_schema=False)
upload_router = APIRouter(tags=["upload"], include_in_schema=False)
chat_router = APIRouter(tags=["chat"], include_in_schema=False)
whatsapp_router = APIRouter(prefix="/admin/whatsapp", tags=["operator"], include_in_schema=False)

_templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@upload_router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request) -> HTMLResponse:
    """Render the self-service document upload UI."""
    context = {
        "request": request,
        "title": "Upload Price Document",
        "subtitle": "Submit price lists, catalog PDFs, or WhatsApp logs for ingestion.",
    }
    return _templates.TemplateResponse(request, "upload.html", context)


@chat_router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request) -> HTMLResponse:
    """Render the lightweight chat prototype that calls the new tool endpoints."""

    dev_query = (request.query_params.get("dev") or "").lower()
    is_dev_mode = dev_query in {"1", "true", "yes"} or settings.environment.lower() not in {"production", "prod"}

    context = {
        "request": request,
        "title": "Pricebot Chat",
        "api_config": {
            "resolve": "/chat/tools/products/resolve",
            "best_price": "/chat/tools/offers/search-best-price",
            "help": "/chat/tools/help",
            "upload": "/documents/upload",
            "document": "/documents",
            "vendors": "/vendors",
            "template_download": "/documents/templates/vendor-price",
            "diagnostics": "/chat/tools/diagnostics",
            "diagnostics_download": "/chat/tools/diagnostics/download",
            "logs": "/chat/tools/logs",
            "logs_download": "/chat/tools/logs/download",
            "export_best_price": "/chat/tools/offers/export",
            "stream": "/chat/stream",
            "jobs": "/documents/jobs",
        },
        "environment": settings.environment,
        "dev_mode": is_dev_mode,
    }
    return _templates.TemplateResponse(request, "chat.html", context)


@router.get("/documents", response_class=HTMLResponse)
async def documents_dashboard(
    request: Request,
    status: Optional[str] = None,
    session: Session = Depends(get_db),
) -> HTMLResponse:
    statement = select(models.SourceDocument).order_by(models.SourceDocument.ingest_started_at.desc()).limit(200)
    if status:
        statement = statement.where(models.SourceDocument.status == status)
    documents = session.exec(statement).all()

    statuses = session.exec(select(models.SourceDocument.status)).all()
    totals = Counter(statuses)

    context_documents = [
        {
            "id": doc.id,
            "file_name": doc.file_name,
            "file_type": doc.file_type,
            "status": doc.status,
            "offer_count": len(doc.offers or []),
            "ingest_started_at": _fmt(doc.ingest_started_at),
            "ingest_completed_at": _fmt(doc.ingest_completed_at),
        }
        for doc in documents
    ]

    context = {
        "request": request,
        "title": "Operator Console",
        "subtitle": "Monitor ingestion jobs and review extracted offers.",
        "documents": context_documents,
        "active_status": status,
        "totals": {
            "total": sum(totals.values()),
            "processed": totals.get("processed", 0),
            "processed_with_warnings": totals.get("processed_with_warnings", 0),
            "failed": totals.get("failed", 0),
        },
    }
    return _templates.TemplateResponse(request, "operator_dashboard.html", context)


@router.get("/documents/{document_id}", response_class=HTMLResponse)
async def document_detail(
    request: Request,
    document_id: UUID,
    session: Session = Depends(get_db),
) -> HTMLResponse:
    document = session.get(models.SourceDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    offers = [
        OfferOut(
            id=offer.id,
            product_id=offer.product_id,
            vendor_id=offer.vendor_id,
            product_name=offer.product.canonical_name if offer.product else "Unknown",
            vendor_name=offer.vendor.name if offer.vendor else "Unknown",
            price=offer.price,
            currency=offer.currency,
            captured_at=offer.captured_at,
            condition=offer.condition,
            quantity=offer.quantity,
            location=offer.location,
        )
        for offer in document.offers or []
    ]

    context = {
        "request": request,
        "title": "Document Detail",
        "document": {
            "id": document.id,
            "file_name": document.file_name,
            "file_type": document.file_type,
            "status": document.status,
            "offer_count": len(offers),
            "ingest_started_at": _fmt(document.ingest_started_at),
            "ingest_completed_at": _fmt(document.ingest_completed_at),
            "extra": document.extra or {},
        },
        "offers": offers,
    }
    return _templates.TemplateResponse(request, "operator_document_detail.html", context)


def _fmt(value: Optional[datetime]) -> str:
    if not value:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M")


@whatsapp_router.get("", response_class=HTMLResponse)
async def whatsapp_dashboard(request: Request, session: Session = Depends(get_db)) -> HTMLResponse:
    chats = session.exec(select(models.WhatsAppChat)).all()
    rows: list[dict] = []
    for chat in chats:
        last = session.exec(
            select(models.WhatsAppMessage)
            .where(models.WhatsAppMessage.chat_id == chat.id)
            .order_by(models.WhatsAppMessage.observed_at.desc())
            .limit(1)
        ).first()
        count_result = session.exec(
            select(func.count()).select_from(models.WhatsAppMessage).where(models.WhatsAppMessage.chat_id == chat.id)
        ).one()
        count = int(count_result[0] if isinstance(count_result, tuple) else count_result)
        vendor = chat.vendor
        rows.append({
            "id": chat.id,
            "title": chat.title,
            "last_message_at": _fmt(last.observed_at) if last else "-",
            "count": count,
            "vendor_id": chat.vendor_id,
            "vendor_name": vendor.name if vendor else None,
        })

    context = {
        "request": request,
        "title": "WhatsApp Chats",
        "chats": rows,
    }
    return _templates.TemplateResponse(request, "whatsapp_dashboard.html", context)


@whatsapp_router.get("/{chat_id}", response_class=HTMLResponse)
async def whatsapp_chat_detail(request: Request, chat_id: UUID, session: Session = Depends(get_db)) -> HTMLResponse:
    chat = session.get(models.WhatsAppChat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    messages = session.exec(
        select(models.WhatsAppMessage)
        .where(models.WhatsAppMessage.chat_id == chat.id)
        .order_by(models.WhatsAppMessage.observed_at.desc())
        .limit(200)
    ).all()

    rows = [
        {
            "observed_at": _fmt(m.observed_at),
            "sender": m.sender_name or ("You" if m.is_outgoing else "Unknown"),
            "text": m.text,
        }
        for m in messages
    ]

    context = {
        "request": request,
        "title": f"Chat: {chat.title}",
        "chat": {
            "id": chat.id,
            "title": chat.title,
            "vendor_id": chat.vendor_id,
            "vendor_name": chat.vendor.name if chat.vendor else None,
        },
        "messages": rows,
        "vendors_endpoint": "/vendors",
        "chat_vendor_endpoint": f"/integrations/whatsapp/chats/{chat.id}/vendor",
    }
    return _templates.TemplateResponse(request, "whatsapp_chat_detail.html", context)
