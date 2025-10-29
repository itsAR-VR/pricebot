from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.ingestion.base import BaseIngestionProcessor, registry
from app.ingestion.text_utils import extract_offers_from_lines
from app.ingestion.types import IngestionResult
from app.services.llm_extraction import (
    ExtractionContext,
    LLMUnavailableError,
    OfferLLMExtractor,
)

try:  # pragma: no cover - optional dependency
    import openai
except ImportError:  # pragma: no cover
    openai = None  # type: ignore

try:  # pragma: no cover - optional dependency
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}

logger = logging.getLogger(__name__)


class DocumentExtractionProcessor(BaseIngestionProcessor):
    name = "document_text"
    supported_suffixes = tuple(_IMAGE_SUFFIXES | {".pdf"})

    def __init__(self, llm_extractor: OfferLLMExtractor | None = None) -> None:
        self._llm_extractor = llm_extractor

    def process(
        self,
        file_path: Path,
        *,
        context: dict[str, Any] | None = None,
    ) -> IngestionResult:
        context = context or {}
        default_vendor = context.get("vendor_name") or file_path.stem
        default_currency = context.get("currency", settings.default_currency)

        try:
            lines = self._extract_lines(file_path)
        except RuntimeError as exc:  # pragma: no cover - runtime path
            return IngestionResult(offers=[], errors=[str(exc)])

        llm_errors: list[str] = []
        llm = self._resolve_llm_extractor(context)
        if llm is not None:
            try:
                base_instructions = (
                    "Treat the extracted text as a vendor price sheet or flyer. Focus on rows that pair product identifiers "
                    "with explicit pricing. Ignore shipping updates, marketing slogans, or unrelated chatter."
                )
                custom_instructions = context.get("llm_instructions") or ""
                extra_instructions = f"{base_instructions} {custom_instructions}".strip()

                media_type = context.get("media_type")
                media_caption = context.get("media_caption")
                if media_type:
                    extra_instructions = (
                        f"This attachment was sent through WhatsApp as a {media_type}. "
                        f"{extra_instructions}"
                    ).strip()
                if media_caption:
                    extra_instructions = (
                        f"{extra_instructions} Caption/notes from the sender: {media_caption}"
                    ).strip()

                offers, warnings = llm.extract_offers_from_lines(
                    lines,
                    context=ExtractionContext(
                        vendor_hint=default_vendor,
                        currency_hint=default_currency,
                        document_name=file_path.name,
                        document_kind=self._document_kind(file_path),
                        extra_instructions=extra_instructions,
                    ),
                )
            except LLMUnavailableError as exc:
                logger.warning("LLM normalization unavailable for document %s: %s", file_path.name, exc)
                llm_errors.append(str(exc))
            else:
                if offers:
                    logger.info(
                        "LLM normalization: processor=document model=%s offers=%d file=%s",
                        getattr(llm, "model", "unknown"),
                        len(offers),
                        file_path.name,
                    )
                    enriched = self._apply_context_metadata(offers, context)
                    return IngestionResult(offers=enriched, errors=warnings)
                logger.warning(
                    "LLM normalization returned no offers for document %s; falling back to heuristics",
                    file_path.name,
                )
                llm_errors.extend(warnings)

        offers, errors = extract_offers_from_lines(
            lines,
            vendor_name=default_vendor,
            default_currency=default_currency,
        )
        offers = self._apply_context_metadata(offers, context)

        combined_errors = llm_errors + errors
        if not offers and not combined_errors:
            combined_errors.append("no pricing information recognized from document")

        return IngestionResult(offers=offers, errors=combined_errors)

    def _resolve_llm_extractor(self, context: dict[str, Any]) -> OfferLLMExtractor | None:
        if context.get("disable_llm"):
            return None
        if self._llm_extractor is not None:
            return self._llm_extractor
        try:
            self._llm_extractor = OfferLLMExtractor()
        except LLMUnavailableError as exc:
            logger.warning("LLM extractor unavailable for document ingestion: %s", exc)
            return None
        return self._llm_extractor

    @staticmethod
    def _document_kind(file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            return "pdf_document"
        if suffix in _IMAGE_SUFFIXES:
            return "image"
        return suffix.lstrip(".") or "unstructured"

    def _extract_lines(self, file_path: Path) -> list[str]:
        """Extract text lines from PDF or image using GPT-5 vision API."""
        suffix = file_path.suffix.lower()
        
        if suffix == ".pdf":
            # Extract text from PDF using pypdf first (faster for text PDFs)
            if PdfReader is None:
                raise RuntimeError("pypdf is required to process PDF documents. Install pricebot[pdf].")
            reader = PdfReader(str(file_path))
            text_chunks: list[str] = []
            for page in reader.pages:
                text = page.extract_text() or ""
                text_chunks.extend(line.strip() for line in text.splitlines() if line.strip())
            
            # If no text extracted (likely a scanned PDF), use GPT-5 OCR
            if not text_chunks:
                return self._extract_text_with_gpt5(file_path)
            return text_chunks

        if suffix in _IMAGE_SUFFIXES:
            # Use GPT-5 vision API for image OCR
            return self._extract_text_with_gpt5(file_path)

        raise RuntimeError(f"Unsupported document type: {suffix}")
    
    def _extract_text_with_gpt5(self, file_path: Path) -> list[str]:
        """Extract text from image using GPT-5 vision API."""
        if openai is None:
            raise RuntimeError("openai is required for OCR. Install: pip install openai")
        
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable must be set for OCR")
        
        # Read and encode image to base64
        with open(file_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
        
        # Determine image mime type
        mime_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
            ".pdf": "application/pdf"
        }
        mime_type = mime_types.get(file_path.suffix.lower(), "image/jpeg")
        
        # Call GPT-5 (or GPT-4o) vision API
        client = openai.OpenAI(api_key=settings.openai_api_key)
        
        try:
            response = client.chat.completions.create(
                model="gpt-4o",  # GPT-4o has vision; update to gpt-5 when available
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Extract all text from this image. Return ONLY the text content, line by line, preserving the structure. Focus on prices, product names, and vendor information."
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=2000,
            )
            
            text = response.choices[0].message.content or ""
            return [line.strip() for line in text.splitlines() if line.strip()]
            
        except Exception as e:
            raise RuntimeError(f"GPT-5 OCR failed: {str(e)}") from e

    def _apply_context_metadata(self, offers: list[RawOffer], context: dict[str, Any]) -> list[RawOffer]:
        if not offers:
            return offers

        message_id = context.get("source_whatsapp_message_id")
        media_caption = context.get("media_caption")
        media_type = context.get("media_type")

        for offer in offers:
            payload = dict(offer.raw_payload or {})
            if message_id:
                payload["source_whatsapp_message_id"] = message_id
                payload.setdefault("source", "whatsapp_media")
            if media_caption:
                captions = payload.get("media_captions")
                if isinstance(captions, list):
                    if media_caption not in captions:
                        captions.append(media_caption)
                elif captions:
                    if captions != media_caption:
                        payload["media_captions"] = [captions, media_caption]
                else:
                    payload["media_captions"] = [media_caption]
            if media_type and not payload.get("media_type"):
                payload["media_type"] = media_type
            offer.raw_payload = payload or None
        return offers


registry.register(DocumentExtractionProcessor())
