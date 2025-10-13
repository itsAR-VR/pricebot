from __future__ import annotations

import re
from typing import Iterable, Tuple

MAX_INLINE_QUANTITY_DIGITS = 4
MIN_IDENTIFIER_DIGITS = 8

from app.core.config import settings
from app.ingestion.types import RawOffer, now_utc

_CURRENCY_TOKENS = ("$", "usd", "cad", "eur", "aed", "gbp", "sgd", "aud", "inr")
_CURRENCY_PATTERN = "(?:" + "|".join(token.replace("$", r"\$") for token in _CURRENCY_TOKENS) + ")"

_PRICE_REGEX = re.compile(
    rf"(?P<prefix>{_CURRENCY_PATTERN})\s*(?P<amount>\d{{2,7}}(?:[.,]\d+)?)"
    rf"|(?P<amount_only>\d{{2,7}}(?:[.,]\d+)?)\s*(?P<suffix>{_CURRENCY_PATTERN})",
    re.IGNORECASE,
)

_QUANTITY_REGEX = re.compile(
    r"(?P<qty>\d{1,4})(?=\s?(?:pcs|pc|units?|qty|x|ct|pieces?|packs?))(?![\w-])",
    re.IGNORECASE,
)

_LEADING_TOKENS = {
    "wtb",
    "wts",
    "wtt",
    "selling",
    "sell",
    "buy",
    "buying",
    "available",
    "need",
    "do",
    "you",
    "have",
    "there",
    "is",
    "looking",
    "for",
    "price",
    "any",
    "take",
    "taking",
    "offers",
}

_TRAILING_TOKENS = {
    "usd",
    "usd.",
    "each",
    "ea",
    "unit",
    "units",
    "firm",
    "obo",
    "net",
}


def parse_offer_line(
    line: str,
    *,
    vendor_name: str,
    default_currency: str | None = None,
    captured_at=None,
    raw_payload: dict | None = None,
) -> Tuple[RawOffer | None, str | None]:
    """Attempt to parse a single text line into a RawOffer."""

    if not line or not line.strip():
        return None, None

    match = _PRICE_REGEX.search(line)
    if not match:
        return None, None

    amount = match.group("amount") or match.group("amount_only")
    if not amount:
        return None, None

    price = _to_float(amount)
    if price is None:
        return None, f"could not parse numeric price from '{amount}'"

    currency_token = match.group("prefix") or match.group("suffix")
    currency = _normalize_currency(currency_token) or (default_currency or settings.default_currency)

    # Extract product text from around the price match
    before = line[: match.start()].strip(" -:|\t")
    after = line[match.end() :].strip(" -:|\t")

    product_source = before or after
    product_name, inferred_quantity, leading_identifiers = _clean_product_name(product_source)
    if not product_name:
        return None, f"could not determine product name from '{line}'"

    quantity = inferred_quantity or _parse_quantity(line)

    payload = {"line": line, **(raw_payload or {})}
    if leading_identifiers:
        payload.setdefault("identifiers", leading_identifiers)

    offer = RawOffer(
        vendor_name=vendor_name,
        product_name=product_name,
        price=price,
        currency=currency,
        quantity=quantity,
        captured_at=captured_at or now_utc(),
        raw_payload=payload,
    )
    return offer, None


def _to_float(value: str) -> float | None:
    try:
        normalized = value.replace(",", "").replace(" ", "")
        return float(normalized)
    except (TypeError, ValueError):
        return None


def _normalize_currency(token: str | None) -> str | None:
    if not token:
        return None
    token = token.strip().upper()
    if token == "$":
        return "USD"
    token = token.replace("$", "")
    if not token:
        return "USD"
    if len(token) == 3:
        return token
    return token


def _clean_product_name(raw_product: str) -> Tuple[str | None, int | None, list[str]]:
    if not raw_product:
        return None, None, []

    tokens = [token for token in re.split(r"\s+", raw_product) if token]
    filtered: list[str] = []
    quantity: int | None = None
    identifiers: list[str] = []

    for idx, token in enumerate(tokens):
        stripped = token.strip(" ,-/")
        if not stripped:
            continue
        lower = stripped.lower()
        if lower in _LEADING_TOKENS and not filtered:
            continue
        if quantity is None and stripped.isdigit() and not filtered:
            if len(stripped) <= MAX_INLINE_QUANTITY_DIGITS:
                quantity = int(stripped)
                continue
            if len(stripped) >= MIN_IDENTIFIER_DIGITS:
                identifiers.append(stripped)
                continue
        filtered.append(stripped)

    while filtered and filtered[0].lower() in _LEADING_TOKENS:
        filtered.pop(0)
    while filtered and filtered[-1].lower() in _TRAILING_TOKENS:
        filtered.pop()

    product = " ".join(filtered).strip(" ,-/")
    if not product:
        return None, quantity, identifiers

    return product, quantity, identifiers


def _parse_quantity(line: str) -> int | None:
    match = _QUANTITY_REGEX.search(line)
    if match:
        try:
            return int(match.group("qty"))
        except ValueError:
            return None
    return None


def extract_offers_from_lines(
    lines: Iterable[str],
    *,
    vendor_name: str,
    default_currency: str | None = None,
) -> Tuple[list[RawOffer], list[str]]:
    offers: list[RawOffer] = []
    errors: list[str] = []

    for idx, line in enumerate(lines, start=1):
        offer, error = parse_offer_line(
            line,
            vendor_name=vendor_name,
            default_currency=default_currency or settings.default_currency,
            raw_payload={"line_number": idx, "raw_lines": [idx]},
        )
        if offer:
            offers.append(offer)
        elif error:
            errors.append(f"line {idx}: {error}")

    return offers, errors
