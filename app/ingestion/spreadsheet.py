from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from app.core.config import settings
from app.ingestion.base import BaseIngestionProcessor, registry
from app.ingestion.types import IngestionResult, RawOffer


class SpreadsheetIngestionProcessor(BaseIngestionProcessor):
    name = "spreadsheet"
    supported_suffixes = (".xlsx", ".xls", ".csv")

    DESCRIPTION_KEYS = {"description", "item", "product", "model", "device", "name"}
    PRICE_KEYS = {"price", "unit price", "usd", "$", "amount"}
    QUANTITY_KEYS = {"qty", "quantity", "available", "stock"}
    SKU_KEYS = {"sku", "model/sku", "model sku", "model number", "model#", "mpn"}
    UPC_KEYS = {"upc", "ean"}
    CONDITION_KEYS = {"condition", "grade"}
    LOCATION_KEYS = {"warehouse", "location", "city"}

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
                errors.append(f"row {row_idx + 1}: missing critical fields (price={price}, description={description})")
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
                raw_payload={k: v for k, v in normalized.items() if v not in (None, "")},
            )
            offers.append(offer)

        if not offers and not errors:
            errors.append("no offers extracted from spreadsheet")

        return IngestionResult(offers=offers, errors=errors)

    def _load_dataframe(self, file_path: Path) -> pd.DataFrame:
        suffix = file_path.suffix.lower()
        if suffix == ".csv":
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)

        df = self._cleanup_dataframe(df)
        if self._mostly_unnamed(df.columns):
            if suffix == ".csv":
                df = pd.read_csv(file_path, header=None)
            else:
                df = pd.read_excel(file_path, header=None)
            df = self._cleanup_dataframe(df)
            df.columns = [f"column_{idx}" for idx in range(len(df.columns))]
        return df

    @staticmethod
    def _cleanup_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        df = df.dropna(how="all")
        df = df.dropna(axis=1, how="all")
        df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)
        return df.reset_index(drop=True)

    @staticmethod
    def _mostly_unnamed(columns: pd.Index) -> bool:
        if not len(columns):
            return True
        unnamed = sum(str(col).lower().startswith("unnamed") for col in columns)
        return unnamed / len(columns) > 0.6

    def _normalize_key(self, key: Any) -> str:
        key_str = str(key).strip().lower()
        return key_str

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
                if value not in (None, ""):
                    return str(value)
        for key, value in row.items():
            if isinstance(value, str) and value:
                return value
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
                if value not in (None, ""):
                    return str(value).strip()
        return None

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        if value in (None, "", "-"):
            return None
        try:
            cleaned = str(value).replace(",", "").replace("$", "").strip()
            if cleaned == "":
                return None
            return float(cleaned)
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
            return int(float(cleaned))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _vendor_from_path(file_path: Path) -> str:
        return file_path.stem.replace("_", " ")


registry.register(SpreadsheetIngestionProcessor())

