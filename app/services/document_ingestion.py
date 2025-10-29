import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session

from app.db import models
from app.ingestion import registry
from app.services.offers import OfferIngestionService

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return a timezone-naive UTC timestamp."""

    now = datetime.now(timezone.utc)
    return now.replace(tzinfo=None)


def _build_ingestion_context(
    processor_name: str,
    vendor_name: str,
    prefer_llm: Optional[bool] = None,
    source_doc: Optional[models.SourceDocument] = None,
    extra_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Prepare processor-specific context hints for ingestion."""

    context: dict[str, Any] = {"vendor_name": vendor_name}

    if processor_name == "whatsapp_text":
        context.update(
            {
                "prefer_llm": True,
                "llm_instructions": (
                    "Treat this as a WhatsApp business chat. Extract only concrete product offers with "
                    "explicit prices, quantities, or deal terms. Ignore greetings, transfers, or discussion."
                ),
            }
        )
    elif processor_name == "document_text":
        context.update(
            {
                "prefer_llm": True,
                "llm_instructions": (
                    "Treat the content as a vendor price sheet. Capture product names, variants, quantities, "
                    "and unit prices from each listed item, ignoring marketing copy or logistics notes."
                ),
            }
        )

    if source_doc and (source_doc.file_type == "whatsapp_media" or source_doc.extra and source_doc.extra.get("source") == "whatsapp_media"):
        context.update(
            {
                "prefer_llm": True,
                "llm_instructions": (
                    "The content originates from a WhatsApp media attachment (photo, screenshot, or PDF). "
                    "Extract structured product offers with explicit prices, quantities, or deal terms. "
                    "Ignore stickers, signatures, or decorations. Associate extracted offers back to the "
                    "originating WhatsApp message if provided."
                ),
            }
        )

    if prefer_llm:
        context["prefer_llm"] = True

    if extra_context:
        context.update({k: v for k, v in extra_context.items() if v is not None})

    return context


def _clear_existing_offers(session: Session, source_doc: models.SourceDocument) -> None:
    offer_ids = [offer.id for offer in source_doc.offers or []]
    if not offer_ids:
        return

    session.exec(
        delete(models.PriceHistory).where(models.PriceHistory.source_offer_id.in_(offer_ids))
    )
    session.exec(delete(models.Offer).where(models.Offer.id.in_(offer_ids)))
    session.flush()


class DocumentIngestResult(BaseModel):
    status: str
    message: str
    document_id: str
    offers_count: int
    warnings: list[str] = []


def ingest_document(
    *,
    session: Session,
    source_doc: models.SourceDocument,
    processor_name: str,
    vendor_name: str,
    file_path: Path,
    prefer_llm: Optional[bool] = None,
    clear_existing: bool = False,
    extra_context: Optional[dict[str, Any]] = None,
) -> DocumentIngestResult:
    """Process a stored document and persist extracted offers."""

    try:
        processor_instance = registry.get(processor_name)
    except KeyError as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=400,
            detail=f"Unknown processor: {processor_name}. Available: {list(registry.processors.keys())}",
        ) from exc

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Stored document file is missing")

    if clear_existing:
        session.refresh(source_doc)
        _clear_existing_offers(session, source_doc)

    now_utc = _utc_now()
    source_doc.status = "processing"
    source_doc.ingest_started_at = now_utc
    source_doc.ingest_completed_at = None
    session.add(source_doc)
    session.flush()

    try:
        context = _build_ingestion_context(
            processor_name,
            vendor_name,
            prefer_llm,
            source_doc=source_doc,
            extra_context=extra_context,
        )
        result = processor_instance.process(file_path, context=context)

        offer_service = OfferIngestionService(session)
        persisted = offer_service.ingest(
            offers=result.offers,
            vendor_name=vendor_name,
            source_document=source_doc,
        )

        if persisted and not source_doc.vendor_id:
            source_doc.vendor_id = persisted[0].vendor_id

        if result.errors:
            logger.warning(
                "Ingestion warnings for document %s: %s",
                source_doc.id,
                result.errors,
            )
            extra = source_doc.extra.copy() if source_doc.extra else {}
            extra["ingestion_errors"] = result.errors
            source_doc.extra = extra
        elif source_doc.extra and "ingestion_errors" in source_doc.extra:
            extra = source_doc.extra.copy()
            extra.pop("ingestion_errors", None)
            source_doc.extra = extra

        source_doc.status = "processed" if not result.errors else "processed_with_warnings"
        source_doc.ingest_completed_at = _utc_now()
        session.add(source_doc)
        session.commit()
        warnings = result.errors or []

        return DocumentIngestResult(
            status=source_doc.status,
            message=f"Processed {len(persisted)} offers",
            document_id=str(source_doc.id),
            offers_count=len(persisted),
            warnings=warnings,
        )

    except Exception as exc:
        session.rollback()
        logger.exception("Document ingestion failed for %s", source_doc.id)
        source_doc.status = "failed"
        source_doc.ingest_completed_at = _utc_now()
        extra = source_doc.extra.copy() if source_doc.extra else {}
        extra.setdefault("errors", [])
        extra["errors"] = [str(exc)]
        source_doc.extra = extra
        session.add(source_doc)
        try:
            session.commit()
        except SQLAlchemyError:  # pragma: no cover - defensive
            session.rollback()
            logger.exception(
                "Failed to persist failure status for document %s", source_doc.id
            )
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(exc)}")


__all__ = ["DocumentIngestResult", "ingest_document", "_utc_now"]
