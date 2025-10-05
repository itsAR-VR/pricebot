from __future__ import annotations

from math import isinf, isnan
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.config import settings
from app.ingestion.base import BaseIngestionProcessor, registry
from app.ingestion.types import IngestionResult, RawOffer


class SpreadsheetIngestionProcessor(BaseIngestionProcessor):
    name = "spreadsheet"
    supported_suffixes = (".xlsx", ".xls", ".csv")

    DESCRIPTION_KEYS = {
        "description",
        "item",
        "product",
        "model",
        "device",
        "name",
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

        for row_idx, row in enumerate(df.to_dict(orient="records")):
            normalized = {self._normalize_key(k): v for k, v in row.items()}

            price = self._extract_price(normalized)
            description = self._extract_description(normalized)

            if price is None or description is None:
                if self._looks_like_header_row(normalized):
                    continue
                errors.append(
                    f"row {row_idx + 1}: missing critical fields (price={price}, description={description})"
                )
                continue

            offer = RawOffer(
                vendor_name=vendor_name,
                product_name=description,
                price=price,
                currency=currency,
                quantity=self._extract_int(normalized, self.QUANTITY_KEYS),
                condition=self._extract_str(normalized, self.CONDITION_KEYS),
                sku=self._extract_str(normalized, self.SKU_KEYS),
                model_number=self._extract_str(normalized, self.SKU_KEYS),
                upc=self._extract_str(normalized, self.UPC_KEYS),
                warehouse=self._extract_str(normalized, self.LOCATION_KEYS),
                raw_payload={k: v for k, v in normalized.items() if not self._is_missing(v)},
            )
            offers.append(offer)

        if not offers and not errors:
            errors.append("no offers extracted from spreadsheet")

        return IngestionResult(offers=offers, errors=errors)

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
            if key in self.DESCRIPTION_KEYS or any(token in key for token in self.DESCRIPTION_KEYS):
                if not self._is_missing(value):
                    return str(value)
        for key, value in row.items():
            if not self._is_missing(value):
                return str(value)
        return None

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

    @staticmethod
    def _is_missing(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str) and value.strip() == "":
            return True
        if isinstance(value, float) and isnan(value):
            return True
        try:
            return bool(pd.isna(value))
        except TypeError:
            return False


registry.register(SpreadsheetIngestionProcessor())
