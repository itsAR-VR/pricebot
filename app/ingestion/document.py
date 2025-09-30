from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.ingestion.base import BaseIngestionProcessor, registry
from app.ingestion.text_utils import extract_offers_from_lines
from app.ingestion.types import IngestionResult

try:  # pragma: no cover - optional dependency
    import openai
except ImportError:  # pragma: no cover
    openai = None  # type: ignore

try:  # pragma: no cover - optional dependency
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}


class DocumentExtractionProcessor(BaseIngestionProcessor):
    name = "document_text"
    supported_suffixes = tuple(_IMAGE_SUFFIXES | {".pdf"})

    def process(self, file_path: Path, *, context: dict[str, Any] | None = None) -> IngestionResult:
        context = context or {}
        default_vendor = context.get("vendor_name") or file_path.stem
        default_currency = context.get("currency", settings.default_currency)

        try:
            lines = self._extract_lines(file_path)
        except RuntimeError as exc:  # pragma: no cover - runtime path
            return IngestionResult(offers=[], errors=[str(exc)])

        offers, errors = extract_offers_from_lines(
            lines,
            vendor_name=default_vendor,
            default_currency=default_currency,
        )

        if not offers and not errors:
            errors.append("no pricing information recognized from document")

        return IngestionResult(offers=offers, errors=errors)

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


registry.register(DocumentExtractionProcessor())
