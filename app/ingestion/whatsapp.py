from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.ingestion.base import BaseIngestionProcessor, registry
from app.ingestion.types import IngestionResult
from app.ingestion.text_utils import parse_offer_line

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


class WhatsAppTextProcessor(BaseIngestionProcessor):
    name = "whatsapp_text"
    supported_suffixes = (".txt",)

    def process(self, file_path: Path, *, context: dict[str, Any] | None = None) -> IngestionResult:
        context = context or {}
        default_vendor = context.get("vendor_name")
        default_currency = context.get("currency", settings.default_currency)

        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except UnicodeDecodeError:
            text = file_path.read_text(encoding="latin-1", errors="ignore")

        offers = []
        errors = []
        current_speaker: str | None = None

        for idx, raw_line in enumerate(text.splitlines(), start=1):
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

        if not offers and not errors:
            errors.append("no offers extracted from WhatsApp transcript")

        return IngestionResult(offers=offers, errors=errors)


registry.register(WhatsAppTextProcessor())
