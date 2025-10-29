from __future__ import annotations

import json
import logging
from dataclasses import dataclass
import sys
from typing import Any, Sequence

from app.core.config import settings
from app.ingestion.types import RawOffer

try:  # pragma: no cover - optional dependency
    import openai
except ImportError:  # pragma: no cover - guard for environments without openai
    openai = None  # type: ignore

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """Raised when an LLM extraction attempt cannot be completed."""


if sys.version_info >= (3, 10):

    @dataclass(slots=True)
    class ExtractionContext:
        """User-provided hints that improve LLM extraction quality."""

        vendor_hint: str | None
        currency_hint: str | None
        document_name: str | None = None
        document_kind: str = "unstructured"
        extra_instructions: str | None = None
        max_lines: int = 240
        max_characters: int = 12000

else:  # pragma: no cover - Python <3.10 compatibility

    @dataclass
    class ExtractionContext:
        """User-provided hints that improve LLM extraction quality."""

        vendor_hint: str | None
        currency_hint: str | None
        document_name: str | None = None
        document_kind: str = "unstructured"
        extra_instructions: str | None = None
        max_lines: int = 240
        max_characters: int = 12000


class OfferLLMExtractor:
    """Helper that prompts an LLM to normalize messy vendor data into RawOffer objects."""

    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, *, model: str | None = None, client: Any | None = None) -> None:
        self.model = model or self.DEFAULT_MODEL
        self._client = client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def extract_offers_from_lines(
        self,
        lines: Sequence[str],
        *,
        context: ExtractionContext,
    ) -> tuple[list[RawOffer], list[str]]:
        """Use an LLM to convert free-form lines into RawOffer objects."""

        formatted_lines, truncated = self._prepare_lines(lines, context.max_lines, context.max_characters)
        if not formatted_lines:
            return [], ["no recognizable content provided to LLM extractor"]

        messages = self._build_messages(formatted_lines, context, truncated)
        client = self._ensure_client()

        response_text: str
        try:
            response = client.chat.completions.create(  # type: ignore[attr-defined]
                model=self.model,
                temperature=0,
                max_tokens=1800,
                response_format={"type": "json_object"},
                messages=messages,
            )
            response_text = response.choices[0].message.content or ""
        except Exception as exc:  # pragma: no cover - network/runtime path
            logger.exception("LLM extraction failed: %s", exc)
            raise LLMUnavailableError(f"LLM extraction failed: {exc}") from exc

        offers, warnings = self._parse_response(response_text, context)
        if truncated:
            warnings.append("input truncated before reaching line/character limit for LLM prompt")
        return offers, warnings

    # ------------------------------------------------------------------
    # Client + prompt helpers
    # ------------------------------------------------------------------
    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client

        if not settings.enable_openai:
            raise LLMUnavailableError("OpenAI usage disabled via settings.enable_openai")
        if openai is None:
            raise LLMUnavailableError("openai package is not installed; install pricebot[llm]")
        if not settings.openai_api_key:
            raise LLMUnavailableError("OPENAI_API_KEY environment variable must be configured")

        self._client = openai.OpenAI(api_key=settings.openai_api_key)
        return self._client

    @staticmethod
    def _prepare_lines(
        lines: Sequence[str],
        max_lines: int,
        max_characters: int,
    ) -> tuple[list[str], bool]:
        prepared: list[str] = []
        truncated = False
        total_chars = 0

        for idx, raw_line in enumerate(lines, start=1):
            stripped = (raw_line or "").strip()
            if not stripped:
                continue

            formatted = f"{idx:04d} | {stripped}"
            line_size = len(formatted)
            if len(prepared) >= max_lines or total_chars + line_size > max_characters:
                truncated = True
                break

            prepared.append(formatted)
            total_chars += line_size

        return prepared, truncated

    @staticmethod
    def _build_messages(
        formatted_lines: Sequence[str],
        context: ExtractionContext,
        truncated: bool,
    ) -> list[dict[str, Any]]:
        vendor_hint = context.vendor_hint or "UNKNOWN"
        currency_hint = (context.currency_hint or settings.default_currency or "USD").upper()
        document_label = context.document_name or "input"

        schema_instruction = (
            "Return JSON with keys 'offers', 'rejected', and 'warnings'. "
            "Each entry in 'offers' must contain: 'product_name' (string), 'price' (number), "
            "'currency' (3-letter uppercase), 'quantity' (integer or null), 'vendor_name' (string), "
            "'vendor_info' (string or null), 'location' (string or null), 'notes' (string or null), "
            "and 'raw_lines' (array of integers referencing the numbered source lines). "
            "Populate 'rejected' with non-offer rows you intentionally skipped, each including "
            "'raw_lines' and 'reason'. Always output valid JSON with no commentary."
        )

        constraint_instruction = (
            "Treat the vendor hint as the default vendor when none is specified per-item. "
            "Do not make up prices. Ignore conversational chatter that does not include an explicit price. "
            "If currency symbols are missing, fall back to the provided currency hint. "
            "Count only real sellable items as offers."
        )

        extra = context.extra_instructions or ""
        truncated_note = "Input truncated." if truncated else ""

        raw_payload = "\n".join(formatted_lines)
        user_text = f"""
You are processing data from a {context.document_kind} named \"{document_label}\".
Vendor hint: {vendor_hint}
Currency hint: {currency_hint}
{truncated_note}

{schema_instruction}
{constraint_instruction}
{extra}

Raw data (each line is prefixed with its line number):
```
{raw_payload}
```
"""

        return [
            {
                "role": "system",
                "content": (
                    "You are Pricebot's normalization agent. Extract product offers from messy vendor data "
                    "and respond with strict JSON that matches the requested schema."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_text,
                    }
                ],
            },
        ]

    # ------------------------------------------------------------------
    # Response parsing helpers
    # ------------------------------------------------------------------
    def _parse_response(
        self,
        response_text: str,
        context: ExtractionContext,
    ) -> tuple[list[RawOffer], list[str]]:
        response_text = response_text.strip()
        if not response_text:
            raise LLMUnavailableError("LLM returned an empty response")

        response_text = self._strip_code_fence(response_text)

        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            logger.warning("Invalid JSON from LLM: %s", response_text)
            raise LLMUnavailableError(f"LLM returned invalid JSON: {exc}") from exc

        offers_payload = payload.get("offers", []) or []
        warnings_payload = payload.get("warnings", []) or []
        rejected_payload = payload.get("rejected", []) or []

        offers: list[RawOffer] = []
        warnings: list[str] = [self._stringify(item) for item in warnings_payload if item]

        for entry in rejected_payload:
            if isinstance(entry, dict):
                reason = self._clean_str(entry.get("reason"))
                if reason:
                    lines_hint = entry.get("raw_lines")
                    warnings.append(f"rejected {lines_hint}: {reason}")

        for raw in offers_payload:
            offer = self._to_raw_offer(raw, context)
            if offer:
                offers.append(offer)
            else:
                warnings.append(f"skipped malformed offer entry: {raw}")

        return offers, warnings

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        if text.startswith("```") and text.endswith("```"):
            body = text.split("\n", 1)[-1]
            if body.endswith("\n```"):
                body = body[: -len("\n```")]
            return body
        return text

    def _to_raw_offer(self, raw: Any, context: ExtractionContext) -> RawOffer | None:
        if not isinstance(raw, dict):
            return None

        product_name = self._clean_str(raw.get("product_name"))
        if not product_name:
            return None

        price = self._to_float(raw.get("price"))
        if price is None:
            return None

        currency = self._clean_str(raw.get("currency")) or context.currency_hint or settings.default_currency
        if not currency:
            currency = "USD"
        currency = currency.upper()

        quantity = self._to_int(raw.get("quantity"))
        vendor_name = self._clean_str(raw.get("vendor_name")) or context.vendor_hint or "Unknown Vendor"

        vendor_info = self._clean_str(raw.get("vendor_info"))
        location = self._clean_str(raw.get("location"))
        notes = self._clean_str(raw.get("notes"))
        raw_lines = raw.get("raw_lines")
        raw_text = self._clean_str(raw.get("raw_text") or raw.get("raw_context"))

        payload = {
            "source": "llm_extractor",
            "model": self.model,
            "document_kind": context.document_kind,
            "document_name": context.document_name,
            "vendor_info": vendor_info,
            "raw_lines": raw_lines,
            "raw_text": raw_text,
        }

        payload = {key: value for key, value in payload.items() if value}

        offer = RawOffer(
            vendor_name=vendor_name,
            product_name=product_name,
            price=price,
            currency=currency,
            quantity=quantity,
            warehouse=location,
            notes=notes,
            raw_payload=payload or None,
        )
        return offer

    @staticmethod
    def _clean_str(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, "", "-"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            try:
                cleaned = str(value).replace(",", "").replace("$", "").strip()
                return float(cleaned)
            except (TypeError, ValueError):
                return None

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value in (None, "", "-"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                cleaned = str(value).replace(",", "").strip()
                if not cleaned:
                    return None
                return int(float(cleaned))
            except (TypeError, ValueError):
                return None

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)


__all__ = ["OfferLLMExtractor", "ExtractionContext", "LLMUnavailableError"]
