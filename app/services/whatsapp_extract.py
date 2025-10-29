from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable
from uuid import UUID

from sqlmodel import Session, select

from app.core.config import settings
from app.db import models
from app.ingestion.text_utils import parse_offer_line
from app.ingestion.types import RawOffer
from app.services.llm_extraction import (
    ExtractionContext,
    LLMUnavailableError,
    OfferLLMExtractor,
)
from app.services.offers import OfferIngestionService


logger = logging.getLogger(__name__)


class WhatsAppExtractionService:
    def __init__(self, session: Session, llm: OfferLLMExtractor | None = None) -> None:
        self.session = session
        self.llm = llm

    def extract_from_chat(
        self,
        chat: models.WhatsAppChat,
        *,
        since: datetime | None = None,
        prefer_llm: bool = False,
        max_messages: int = 500,
    ) -> dict:
        stmt = (
            select(models.WhatsAppMessage)
            .where(models.WhatsAppMessage.chat_id == chat.id)
            .order_by(models.WhatsAppMessage.observed_at.desc())
            .limit(max_messages)
        )
        if since is not None:
            stmt = stmt.where(models.WhatsAppMessage.observed_at >= since)
        messages = list(reversed(self.session.exec(stmt).all()))  # oldest-first
        if not messages:
            return {"offers": 0, "warnings": 0}

        mapped_vendor = self.session.get(models.Vendor, chat.vendor_id) if chat.vendor_id else None
        lines = [m.text for m in messages if m.text and m.text.strip()]
        default_vendor = (mapped_vendor.name if mapped_vendor else None) or chat.title or "WhatsApp Vendor"
        currency = settings.default_currency

        heuristic_offers: list[RawOffer] = []
        errors: list[str] = []
        for m in messages:
            speaker = m.sender_name or default_vendor
            offer, err = parse_offer_line(
                m.text,
                vendor_name=speaker,
                default_currency=currency,
                raw_payload={"message_id": m.message_id, "observed_at": m.observed_at.isoformat()},
            )
            if offer:
                if mapped_vendor:
                    offer.vendor_name = mapped_vendor.name
                # tie offer to message id and observed time
                try:
                    offer.captured_at = m.observed_at
                except Exception:
                    pass
                if offer.raw_payload is None:
                    offer.raw_payload = {}
                offer.raw_payload["source_whatsapp_message_id"] = str(m.id)
                heuristic_offers.append(offer)
            elif err and ("$" in m.text or "usd" in m.text.lower()):
                errors.append(err)

        offers: list[RawOffer] = heuristic_offers
        warnings: list[str] = []

        if prefer_llm or not offers:
            llm = self._ensure_llm()
            if llm is not None:
                try:
                    llm_offers, llm_warnings = llm.extract_offers_from_lines(
                        lines,
                        context=ExtractionContext(
                            vendor_hint=default_vendor,
                            currency_hint=currency,
                            document_name=f"whatsapp:{chat.title}",
                            document_kind="whatsapp_live",
                            extra_instructions=(
                                "Messages are from WhatsApp Web. Return only rows with a product and a price."
                            ),
                        ),
                    )
                    if prefer_llm or not offers:
                        offers = llm_offers
                        warnings.extend(llm_warnings)
                except LLMUnavailableError as exc:
                    warnings.append(str(exc))
        if mapped_vendor:
            for offer in offers:
                offer.vendor_name = mapped_vendor.name

        # Persist via OfferIngestionService, with a synthetic SourceDocument for traceability
        source_doc = self._create_source_document(chat, vendor_id=mapped_vendor.id if mapped_vendor else None)
        ingestion = OfferIngestionService(self.session)
        persisted = ingestion.ingest(
            offers,
            vendor_name=mapped_vendor.name if mapped_vendor else default_vendor,
            source_document=source_doc,
        )

        # Update vendor on document for traceability
        if persisted and not source_doc.vendor_id:
            source_doc.vendor_id = persisted[0].vendor_id

        # Mark document status
        source_doc.status = "processed_with_warnings" if warnings or errors else "processed"
        source_doc.ingest_started_at = source_doc.ingest_started_at or datetime.utcnow()
        source_doc.ingest_completed_at = datetime.utcnow()
        extra = (source_doc.extra or {}) | {
            "source": "whatsapp_live",
            "chat_id": str(chat.id),
            "offers": len(persisted),
            "warnings": warnings + errors,
        }
        source_doc.extra = extra

        # Update chat last_extracted_at
        try:
            chat.last_extracted_at = datetime.utcnow()
        except Exception:
            pass

        return {"offers": len(persisted), "warnings": len(warnings) + len(errors), "document_id": str(source_doc.id)}

    def _create_source_document(self, chat: models.WhatsAppChat, *, vendor_id: UUID | None = None) -> models.SourceDocument:
        name = f"{chat.title}.whatsapp"
        path = f"whatsapp://chat/{chat.id}"
        doc = models.SourceDocument(
            vendor_id=vendor_id,
            file_name=name,
            file_type="whatsapp_live",
            storage_path=path,
            status="processing",
            extra={"chat_title": chat.title},
        )
        self.session.add(doc)
        self.session.flush()
        return doc

    def _ensure_llm(self) -> OfferLLMExtractor | None:
        if self.llm is not None:
            return self.llm
        try:
            self.llm = OfferLLMExtractor()
        except LLMUnavailableError:
            return None
        return self.llm
