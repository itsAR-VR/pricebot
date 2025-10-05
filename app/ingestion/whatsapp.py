from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.ingestion.base import BaseIngestionProcessor, registry
from app.ingestion.types import IngestionResult, RawOffer
from app.ingestion.text_utils import parse_offer_line
from app.services.llm_extraction import (
    ExtractionContext,
    LLMUnavailableError,
    OfferLLMExtractor,
)

_TIME_PATTERN = re.compile(r"^\d{1,2}:\d{2}")
_SKIP_PREFIXES = (
    "groups",
    "business",
    "purchase",
    "wa business",
    "chats",
    "calls",
    "updates",
    "tools",
    "voice call",
    "video call",
    "you joined",
    "messages and calls are end-to-end encrypted",
    "this chat is with a business account",
    "missed voice call",
    "missed video call",
    "security code changed",
    "added you",
    "media omitted",
)

_REACTION_PREFIXES = ("you reacted", "tony reacted", "reacted")

logger = logging.getLogger(__name__)


class WhatsAppTextProcessor(BaseIngestionProcessor):
    name = "whatsapp_text"
    supported_suffixes = (".txt",)

    def __init__(self, llm_extractor: OfferLLMExtractor | None = None) -> None:
        self._llm_extractor = llm_extractor

    def process(self, file_path: Path, *, context: dict[str, Any] | None = None) -> IngestionResult:
        context = context or {}
        default_vendor = context.get("vendor_name")
        default_currency = context.get("currency", settings.default_currency)

        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except UnicodeDecodeError:
            text = file_path.read_text(encoding="latin-1", errors="ignore")

        raw_lines = text.splitlines()

        offers: list[RawOffer] = []
        errors: list[str] = []
        current_speaker: str | None = None

        for idx, raw_line in enumerate(raw_lines, start=1):
            line = raw_line.strip()
            if not line:
                continue

            lowered = line.lower()
            if any(lowered.startswith(prefix) for prefix in _SKIP_PREFIXES):
                continue
            if any(lowered.startswith(prefix) for prefix in _REACTION_PREFIXES):
                continue
            if lowered in {"photo", "video", "missed voice call", "missed video call"}:
                continue
            if _TIME_PATTERN.match(line):
                current_speaker = None
                continue

            if line.endswith(":") and len(line) <= 40:
                current_speaker = line.rstrip(": ")
                continue

            speaker = default_vendor or current_speaker or "WhatsApp Vendor"

            offer, error = parse_offer_line(
                line,
                vendor_name=speaker,
                default_currency=default_currency,
                raw_payload={"line_number": idx, "speaker": speaker},
            )
            if offer:
                offers.append(offer)
            elif error:
                if "$" in line or "usd" in line.lower():
                    errors.append(f"line {idx}: {error}")

        prefer_llm = bool(context.get("prefer_llm"))
        use_llm = prefer_llm or not offers

        llm_errors: list[str] = []
        if use_llm:
            llm = self._resolve_llm_extractor(context)
            if llm is not None:
                try:
                    llm_offers, warnings = llm.extract_offers_from_lines(
                        raw_lines,
                        context=ExtractionContext(
                            vendor_hint=default_vendor or "WhatsApp Vendor",
                            currency_hint=default_currency,
                            document_name=file_path.name,
                            document_kind="whatsapp_transcript",
                            extra_instructions=(
                                "Messages are from a WhatsApp chat. Only return rows that clearly describe a "
                                "product AND a price. Ignore greetings, reactions, and status updates."
                            ),
                        ),
                    )
                except LLMUnavailableError as exc:
                    llm_errors.append(str(exc))
                else:
                    if llm_offers:
                        if prefer_llm and offers:
                            combined_errors = errors + warnings
                            return IngestionResult(offers=llm_offers, errors=combined_errors)
                        if not offers:
                            return IngestionResult(offers=llm_offers, errors=warnings)
                    llm_errors.extend(warnings)

        if offers:
            combined_errors = errors + llm_errors
            return IngestionResult(offers=offers, errors=combined_errors)

        combined_errors = errors + llm_errors
        if not combined_errors:
            combined_errors.append("no offers extracted from WhatsApp transcript")

        return IngestionResult(offers=[], errors=combined_errors)

    def _resolve_llm_extractor(self, context: dict[str, Any]) -> OfferLLMExtractor | None:
        if context.get("disable_llm"):
            return None
        if self._llm_extractor is not None:
            return self._llm_extractor
        try:
            self._llm_extractor = OfferLLMExtractor()
        except LLMUnavailableError as exc:
            logger.debug("LLM extractor unavailable: %s", exc)
            return None
        return self._llm_extractor


registry.register(WhatsAppTextProcessor())
