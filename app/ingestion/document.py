from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import settings
from app.ingestion.base import BaseIngestionProcessor, registry
from app.ingestion.text_utils import extract_offers_from_lines
from app.ingestion.types import IngestionResult

try:  # pragma: no cover - optional dependency
    from PIL import Image
except ImportError:  # pragma: no cover - gracefully handled in runtime
    Image = None  # type: ignore

try:  # pragma: no cover - optional dependency
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None  # type: ignore

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
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            if PdfReader is None:
                raise RuntimeError("pypdf is required to process PDF documents. Install pricebot[pdf].")
            reader = PdfReader(str(file_path))
            text_chunks: list[str] = []
            for page in reader.pages:
                text = page.extract_text() or ""
                text_chunks.extend(line.strip() for line in text.splitlines() if line.strip())
            return text_chunks

        if suffix in _IMAGE_SUFFIXES:
            if Image is None or pytesseract is None:
                raise RuntimeError("pytesseract and pillow are required for image OCR. Install pricebot[ocr].")
            with Image.open(file_path) as image:
                text = pytesseract.image_to_string(image)
            return [line.strip() for line in text.splitlines() if line.strip()]

        raise RuntimeError(f"Unsupported document type: {suffix}")


registry.register(DocumentExtractionProcessor())
