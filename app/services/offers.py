from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import logging

from sqlmodel import Session, select

from app.core.config import settings
from app.db import models
from app.ingestion.types import RawOffer


logger = logging.getLogger(__name__)
MAX_SIGNED_INT = 2_147_483_647


class OfferIngestionService:
    """Co-ordinates persistence of RawOffer items into the relational model."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def ingest(
        self,
        offers: Iterable[RawOffer],
        *,
        vendor_name: str | None = None,
        source_document: models.SourceDocument | None = None,
    ) -> list[models.Offer]:
        persisted_offers: list[models.Offer] = []
        vendor_cache: dict[str, models.Vendor] = {}

        for payload in offers:
            vendor = self._get_or_create_vendor(payload.vendor_name or vendor_name, vendor_cache)
            product = self._get_or_create_product(payload, vendor)

            quantity = payload.quantity
            raw_payload_data: dict | None = None
            if quantity is not None and abs(quantity) > MAX_SIGNED_INT:
                logger.warning(
                    "Dropping out-of-range quantity during ingestion",
                    extra={
                        "product": payload.product_name,
                        "vendor": vendor.name,
                        "quantity": quantity,
                    },
                )
                raw_payload_data = dict(payload.raw_payload) if payload.raw_payload else {}
                raw_payload_data["dropped_quantity"] = quantity
                quantity = None
            elif payload.raw_payload:
                raw_payload_data = payload.raw_payload

            # Dedup by source_whatsapp_message_id if present
            source_whatsapp_message_id = None
            if payload.raw_payload and payload.raw_payload.get("source_whatsapp_message_id"):
                try:
                    from uuid import UUID as _UUID
                    source_whatsapp_message_id = _UUID(str(payload.raw_payload["source_whatsapp_message_id"]))
                except Exception:
                    source_whatsapp_message_id = None
            if source_whatsapp_message_id is not None:
                existing = self.session.exec(
                    select(models.Offer).where(models.Offer.source_whatsapp_message_id == source_whatsapp_message_id)
                ).first()
                if existing:
                    persisted_offers.append(existing)
                    continue

            offer = models.Offer(
                product_id=product.id,
                vendor_id=vendor.id,
                price=payload.price,
                currency=payload.currency or settings.default_currency,
                quantity=quantity,
                condition=payload.condition,
                location=payload.warehouse,
                captured_at=self._normalize_utc(payload.captured_at),
                notes=payload.notes,
                raw_payload=raw_payload_data,
                source_document_id=source_document.id if source_document else None,
                source_whatsapp_message_id=source_whatsapp_message_id,
            )
            self.session.add(offer)
            self.session.flush()
            self._record_price_history(offer)
            persisted_offers.append(offer)

        return persisted_offers

    def _get_or_create_vendor(
        self,
        vendor_name: str | None,
        vendor_cache: dict[str, models.Vendor],
    ) -> models.Vendor:
        if not vendor_name:
            raise ValueError("Vendor name is required for offer ingestion")

        vendor_name_key = vendor_name.strip().lower()

        if vendor_name_key in vendor_cache:
            return vendor_cache[vendor_name_key]

        statement = select(models.Vendor).where(models.Vendor.name == vendor_name)
        vendor = self.session.exec(statement).one_or_none()
        if not vendor:
            vendor = models.Vendor(name=vendor_name)
            self.session.add(vendor)
            self.session.flush()

        vendor_cache[vendor_name_key] = vendor
        return vendor

    def _get_or_create_product(
        self,
        payload: RawOffer,
        vendor: models.Vendor,
    ) -> models.Product:
        lookup_fields: list[tuple[str, str | None]] = [
            ("model_number", payload.model_number or payload.sku),
            ("upc", payload.upc),
        ]

        for field_name, value in lookup_fields:
            if not value:
                continue
            statement = select(models.Product).where(getattr(models.Product, field_name) == value)
            product = self.session.exec(statement).one_or_none()
            if product:
                return product

        statement = select(models.Product).where(models.Product.canonical_name == payload.product_name)
        product = self.session.exec(statement).one_or_none()
        if product:
            return product

        product = models.Product(
            canonical_name=payload.product_name,
            brand=None,
            model_number=payload.model_number or payload.sku,
            upc=payload.upc,
            category=None,
            default_vendor_id=vendor.id,
            spec={},
        )
        self.session.add(product)
        self.session.flush()

        if payload.product_name:
            alias = models.ProductAlias(
                product_id=product.id,
                alias_text=payload.product_name,
                source_vendor_id=vendor.id,
            )
            self.session.add(alias)

        return product

    def _record_price_history(self, offer: models.Offer) -> None:
        """Maintain price history spans for the given offer.

        Hardened logic:
        1. Find any open (valid_to=NULL) price span for this product/vendor
        2. If same price/currency and newer timestamp: skip (no change)
        3. If price changed: close the old span and create a new one
        4. Handle out-of-order insertions by setting valid_to on new entry
        5. Log price changes for auditing
        """
        captured_at = self._normalize_utc(offer.captured_at)

        # Find the currently open price span (if any)
        statement = (
            select(models.PriceHistory)
            .where(
                (models.PriceHistory.product_id == offer.product_id)
                & (models.PriceHistory.vendor_id == offer.vendor_id)
                & (models.PriceHistory.valid_to.is_(None))
            )
            .order_by(models.PriceHistory.valid_from.desc())
        )
        open_entry = self.session.exec(statement).first()

        if open_entry:
            open_entry_valid_from = self._normalize_utc(open_entry.valid_from)

            # Same price/currency - no change needed if this is newer or same time
            if (
                open_entry.price == offer.price
                and open_entry.currency == offer.currency
                and captured_at >= open_entry_valid_from
            ):
                logger.debug(
                    "Price unchanged for product=%s vendor=%s (price=%.2f %s)",
                    offer.product_id,
                    offer.vendor_id,
                    offer.price,
                    offer.currency,
                )
                return

            # Price changed - close the old span if this offer is newer
            if captured_at >= open_entry_valid_from:
                logger.info(
                    "Price change detected: product=%s vendor=%s old=%.2f new=%.2f %s",
                    offer.product_id,
                    offer.vendor_id,
                    open_entry.price,
                    offer.price,
                    offer.currency,
                )
                open_entry.valid_to = captured_at
                self.session.add(open_entry)

        # Check for uniqueness constraint: valid_from must be unique per product/vendor
        existing_at_time = self.session.exec(
            select(models.PriceHistory).where(
                (models.PriceHistory.product_id == offer.product_id)
                & (models.PriceHistory.vendor_id == offer.vendor_id)
                & (models.PriceHistory.valid_from == captured_at)
            )
        ).first()

        if existing_at_time:
            # Already have an entry at this exact time - update it instead
            logger.debug(
                "Updating existing price history entry at %s for product=%s",
                captured_at,
                offer.product_id,
            )
            existing_at_time.price = offer.price
            existing_at_time.currency = offer.currency
            existing_at_time.source_offer_id = offer.id
            self.session.add(existing_at_time)
            return

        # Determine valid_to for the new entry
        # If this is an out-of-order insertion (before the open entry), close this new span
        new_valid_to = None
        if open_entry and captured_at < self._normalize_utc(open_entry.valid_from):
            # Out-of-order: this is an older price, set its end to when the next price started
            new_valid_to = self._normalize_utc(open_entry.valid_from)
            logger.debug(
                "Out-of-order price insertion: setting valid_to=%s for product=%s",
                new_valid_to,
                offer.product_id,
            )

        history_entry = models.PriceHistory(
            product_id=offer.product_id,
            vendor_id=offer.vendor_id,
            price=offer.price,
            currency=offer.currency,
            valid_from=captured_at,
            valid_to=new_valid_to,
            source_offer_id=offer.id,
        )
        self.session.add(history_entry)
        logger.debug(
            "Created price history entry: product=%s vendor=%s price=%.2f valid_from=%s",
            offer.product_id,
            offer.vendor_id,
            offer.price,
            captured_at,
        )

    @staticmethod
    def _normalize_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
