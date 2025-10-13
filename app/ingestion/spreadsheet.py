from __future__ import annotations

import logging
from math import isinf, isnan
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.config import settings
from app.ingestion.base import BaseIngestionProcessor, registry
from app.ingestion.types import IngestionResult, RawOffer
from app.services.llm_extraction import (
    ExtractionContext,
    LLMUnavailableError,
    OfferLLMExtractor,
)

logger = logging.getLogger(__name__)


class SpreadsheetIngestionProcessor(BaseIngestionProcessor):
    name = "spreadsheet"
    supported_suffixes = (".xlsx", ".xls", ".csv")

    TITLE_KEYS = {
        "item",
        "product",
        "model",
        "device",
        "name",
        "title",
    }
    DETAIL_KEYS = {
        "description",
        "details",
        "notes",
        "spec",
        "specs",
        "comment",
        "comments",
        "features",
        "feature",
    }
    PRICE_KEYS = {
        "price",
        "unit price",
        "sell price",
        "offer price",
        "amount",
        "usd",
        "cost",
        "net price",
    }
    QUANTITY_KEYS = {
        "qty",
        "quantity",
        "available",
        "stock",
        "qty available",
        "moq",
        "minimum order quantity",
        "min order",
        "min qty",
    }
    SKU_KEYS = {
        "sku",
        "model sku",
        "model number",
        "model#",
        "mpn",
        "part number",
    }
    UPC_KEYS = {"upc", "ean"}
    CONDITION_KEYS = {"condition", "grade"}
    LOCATION_KEYS = {"warehouse", "location", "city", "hub", "region"}

    DESCRIPTION_KEYS = TITLE_KEYS | DETAIL_KEYS
    PLACEHOLDER_STRINGS = {
        "n/a",
        "na",
        "none",
        "null",
        "no data",
        "nodata",
        "not available",
        "tbd",
        "pending",
        "unknown",
        "-",
        "--",
    }

    HEADER_MATCH_THRESHOLD = 2
    HEADER_KEYS = (
        DESCRIPTION_KEYS
        | PRICE_KEYS
        | QUANTITY_KEYS
        | SKU_KEYS
        | UPC_KEYS
        | CONDITION_KEYS
        | LOCATION_KEYS
    )

    def __init__(self, llm_extractor: OfferLLMExtractor | None = None) -> None:
        self._llm_extractor = llm_extractor

    def process(self, file_path: Path, *, context: dict[str, Any] | None = None) -> IngestionResult:
        context = context or {}
        vendor_name = context.get("vendor_name") or self._vendor_from_path(file_path)
        currency = context.get("currency", settings.default_currency)

        try:
            df = self._load_dataframe(file_path)
        except Exception as exc:  # pragma: no cover - surface parse error
            return IngestionResult(offers=[], errors=[f"failed to load spreadsheet: {exc}"])

        offers: list[RawOffer] = []
        errors: list[str] = []

        records = df.to_dict(orient="records")
        formatted_for_llm = self._format_rows_for_llm(records, df.columns)

        for row_idx, row in enumerate(records):
            normalized = {self._normalize_key(k): v for k, v in row.items()}

            price = self._extract_price(normalized)
            product_name = self._extract_description(normalized)

            if price is None or product_name is None:
                if self._looks_like_header_row(normalized):
                    continue
                errors.append(
                    f"row {row_idx + 1}: missing critical fields (price={price}, product_name={product_name})"
                )
                continue

            notes = self._extract_notes(normalized, product_name)

            offer = RawOffer(
                vendor_name=vendor_name,
                product_name=product_name,
                price=price,
                currency=currency,
                quantity=self._extract_int(normalized, self.QUANTITY_KEYS),
                condition=self._extract_str(normalized, self.CONDITION_KEYS),
                sku=self._extract_str(normalized, self.SKU_KEYS),
                model_number=self._extract_str(normalized, self.SKU_KEYS),
                upc=self._extract_str(normalized, self.UPC_KEYS),
                warehouse=self._extract_str(normalized, self.LOCATION_KEYS),
                notes=notes,
                raw_payload=self._build_raw_payload(row_idx, normalized),
            )
            offers.append(offer)

        if not offers and not errors:
            errors.append("no offers extracted from spreadsheet")
        prefer_llm = bool(context.get("prefer_llm"))
        use_llm = (prefer_llm or not offers) and bool(formatted_for_llm)
        llm_errors: list[str] = []

        if use_llm:
            llm = self._resolve_llm_extractor(context)
            if llm is not None:
                try:
                    base_instructions = (
                        "Rows describe vendor offers in a spreadsheet. Extract real sale items with prices. "
                        "Ignore header, subtotal, and summary rows. Use quantity, SKU, and description columns when present."
                    )
                    custom_prompt = context.get("llm_instructions")
                    extra_instructions = (
                        f"{base_instructions} {custom_prompt}".strip() if custom_prompt else base_instructions
                    )

                    llm_offers, warnings = llm.extract_offers_from_lines(
                        formatted_for_llm,
                        context=ExtractionContext(
                            vendor_hint=vendor_name,
                            currency_hint=currency,
                            document_name=file_path.name,
                            document_kind="spreadsheet",
                            extra_instructions=extra_instructions,
                        ),
                    )
                except LLMUnavailableError as exc:
                    logger.warning("LLM normalization unavailable for spreadsheet ingestion: %s", exc)
                    llm_errors.append(str(exc))
                else:
                    if llm_offers:
                        logger.info(
                            "LLM normalization: processor=spreadsheet model=%s offers=%d prefer_llm=%s",
                            getattr(llm, "model", "unknown"),
                            len(llm_offers),
                            prefer_llm,
                        )
                        merged_offers = self._merge_llm_offers(llm_offers, offers)
                        combined_errors = errors + warnings
                        return IngestionResult(offers=merged_offers, errors=combined_errors)
                    logger.warning("LLM normalization returned no spreadsheet offers; using heuristics instead")
                    llm_errors.extend(warnings)

        return IngestionResult(offers=offers, errors=errors + llm_errors)

    def _load_dataframe(self, file_path: Path) -> pd.DataFrame:
        suffix = file_path.suffix.lower()
        df = self._read_raw(file_path, suffix, header=0)
        df = self._cleanup_dataframe(df)

        if self._headers_valid(df.columns):
            return df

        headerless = self._cleanup_dataframe(self._read_raw(file_path, suffix, header=None))
        inferred = self._apply_inferred_header(headerless)
        if inferred is not None:
            return inferred

        # Fallback to a generic column naming for downstream parsing
        headerless.columns = [f"column_{idx}" for idx in range(len(headerless.columns))]
        return headerless

    @staticmethod
    def _read_raw(file_path: Path, suffix: str, header: int | None) -> pd.DataFrame:
        if suffix == ".csv":
            return pd.read_csv(file_path, header=header)
        return pd.read_excel(file_path, header=header)

    @staticmethod
    def _cleanup_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        df = df.dropna(how="all")
        df = df.dropna(axis=1, how="all")
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
        return df.reset_index(drop=True)

    def _headers_valid(self, columns: pd.Index) -> bool:
        normalized = [self._normalize_key(col) for col in columns]
        hits = sum(1 for key in normalized if self._is_header_key(key))
        return hits >= self.HEADER_MATCH_THRESHOLD

    def _apply_inferred_header(self, df: pd.DataFrame) -> pd.DataFrame | None:
        header_row_idx = self._infer_header_row(df)
        if header_row_idx is None:
            return None
        header_values = [str(value).strip() for value in df.iloc[header_row_idx].tolist()]
        rows = df.iloc[header_row_idx + 1 :].reset_index(drop=True)
        if not len(rows):
            return None
        rows.columns = header_values
        return rows

    @staticmethod
    def _mostly_unnamed(columns: pd.Index) -> bool:
        if not len(columns):
            return True
        unnamed = sum(str(col).lower().startswith("unnamed") for col in columns)
        return unnamed / len(columns) > 0.6

    def _infer_header_row(self, df: pd.DataFrame) -> int | None:
        max_scan = min(len(df), 15)
        best_idx = None
        best_score = 0
        for idx in range(max_scan):
            row = df.iloc[idx].tolist()
            normalized = [self._normalize_key(value) for value in row if not self._is_missing(value)]
            if not normalized:
                continue
            score = sum(1 for key in normalized if self._is_header_key(key))
            if score > best_score and score >= self.HEADER_MATCH_THRESHOLD:
                best_score = score
                best_idx = idx
        return best_idx

    def _normalize_key(self, key: Any) -> str:
        key_str = str(key).strip().lower()
        key_str = key_str.replace("\n", " ")
        translation = str.maketrans({"/": " ", "-": " ", "#": " ", ".": " ", "(": " ", ")": " ", ":": " ", "&": " ", "@": " ", ",": " "})
        key_str = key_str.translate(translation)
        key_str = " ".join(token for token in key_str.split() if token)
        return key_str

    def _is_header_key(self, key: str) -> bool:
        if key in self.HEADER_KEYS:
            return True
        return False

    def _extract_price(self, row: dict[str, Any]) -> float | None:
        for key, value in row.items():
            if key in self.PRICE_KEYS or any(token in key for token in self.PRICE_KEYS):
                price = self._parse_float(value)
                if price is not None:
                    return price
        numeric_candidates = [self._parse_float(v) for v in row.values()]
        numeric_candidates = [v for v in numeric_candidates if v is not None]
        if numeric_candidates:
            return numeric_candidates[0]
        return None

    def _extract_description(self, row: dict[str, Any]) -> str | None:
        for key, value in row.items():
            if key in self.TITLE_KEYS or any(token in key for token in self.TITLE_KEYS):
                if not self._is_missing(value):
                    return str(value).strip()
        for key, value in row.items():
            if key in self.DETAIL_KEYS or any(token in key for token in self.DETAIL_KEYS):
                if not self._is_missing(value):
                    return str(value).strip()
        for key, value in row.items():
            if not self._is_missing(value):
                return str(value).strip()
        return None

    def _extract_notes(self, row: dict[str, Any], product_name: str | None) -> str | None:
        if not row:
            return None
        details: list[str] = []
        normalized_product = product_name.strip().lower() if isinstance(product_name, str) else None

        for key, value in row.items():
            if key in self.DETAIL_KEYS or any(token in key for token in self.DETAIL_KEYS):
                if self._is_missing(value):
                    continue
                text = str(value).strip()
                if not text:
                    continue
                if normalized_product and text.lower().strip() == normalized_product:
                    continue
                if text not in details:
                    details.append(text)

        return "\n".join(details) if details else None

    def _extract_int(self, row: dict[str, Any], keys: set[str]) -> int | None:
        for key in row:
            if key in keys or any(token in key for token in keys):
                value = self._parse_int(row[key])
                if value is not None:
                    return value
        return None

    def _extract_str(self, row: dict[str, Any], keys: set[str]) -> str | None:
        for key in row:
            if key in keys or any(token in key for token in keys):
                value = row[key]
                if not self._is_missing(value):
                    return str(value).strip()
        return None

    def _looks_like_header_row(self, row: dict[str, Any]) -> bool:
        normalized_keys = [self._normalize_key(value) for value in row.values() if not self._is_missing(value)]
        matches = sum(1 for key in normalized_keys if self._is_header_key(key))
        return matches >= self.HEADER_MATCH_THRESHOLD

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        if value in (None, "", "-"):
            return None
        try:
            cleaned = str(value).replace(",", "").replace("$", "").strip()
            if cleaned == "":
                return None
            parsed = float(cleaned)
            if isnan(parsed) or isinf(parsed):
                return None
            return parsed
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_int(value: Any) -> int | None:
        if value in (None, "", "-"):
            return None
        try:
            cleaned = str(value).replace(",", "").strip()
            if cleaned == "":
                return None
            parsed = float(cleaned)
            if isnan(parsed) or isinf(parsed):
                return None
            return int(parsed)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _vendor_from_path(file_path: Path) -> str:
        return file_path.stem.replace("_", " ")

    @classmethod
    def _is_missing(cls, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            stripped = value.strip()
            if stripped == "":
                return True
            simplified = "".join(ch if ch.isalnum() else " " for ch in stripped.lower())
            simplified = " ".join(simplified.split())
            if simplified in cls.PLACEHOLDER_STRINGS:
                return True
        if isinstance(value, float) and isnan(value):
            return True
        try:
            return bool(pd.isna(value))
        except TypeError:
            return False

    def _build_raw_payload(self, row_idx: int, normalized: dict[str, Any]) -> dict[str, Any]:
        payload = {k: v for k, v in normalized.items() if not self._is_missing(v)}
        payload["row_index"] = row_idx + 1
        payload["raw_lines"] = [row_idx + 1]
        payload["source"] = "spreadsheet_heuristic"
        return payload

    def _format_rows_for_llm(self, records: list[dict[str, Any]], columns: pd.Index) -> list[str]:
        if not records:
            return []

        column_aliases = [
            (col, (str(col).strip() or f"column_{idx}"))
            for idx, col in enumerate(columns, start=1)
        ]

        lines: list[str] = []
        for idx, row in enumerate(records, start=1):
            parts: list[str] = []
            for original_name, alias in column_aliases:
                raw_value = row.get(original_name)
                if self._is_missing(raw_value):
                    continue
                parts.append(f"{alias}: {raw_value}")
            if not parts:
                continue
            lines.append(f"Row {idx}: " + " | ".join(parts))
        return lines

    def _merge_llm_offers(self, llm_offers: list[RawOffer], heuristic_offers: list[RawOffer]) -> list[RawOffer]:
        if not heuristic_offers:
            return llm_offers

        llm_rows: set[int] = set()
        for offer in llm_offers:
            raw_payload = offer.raw_payload or {}
            rows = raw_payload.get("raw_lines")
            if isinstance(rows, list):
                for value in rows:
                    if isinstance(value, int):
                        llm_rows.add(value)

        remaining = []
        for offer in heuristic_offers:
            payload = offer.raw_payload or {}
            row_index = payload.get("row_index")
            if isinstance(row_index, int) and row_index in llm_rows:
                continue
            remaining.append(offer)

        return llm_offers + remaining

    def _resolve_llm_extractor(self, context: dict[str, Any]) -> OfferLLMExtractor | None:
        if context.get("disable_llm"):
            return None
        if self._llm_extractor is not None:
            return self._llm_extractor
        try:
            self._llm_extractor = OfferLLMExtractor()
        except LLMUnavailableError as exc:
            logger.warning("LLM extractor unavailable for spreadsheet ingestion: %s", exc)
            return None
        return self._llm_extractor


registry.register(SpreadsheetIngestionProcessor())
