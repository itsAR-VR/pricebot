from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlmodel import Session, select

from app.core.config import settings
from app.db import models
from app.ingestion.types import RawOffer


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

            offer = models.Offer(
                product_id=product.id,
                vendor_id=vendor.id,
                price=payload.price,
                currency=payload.currency or settings.default_currency,
                quantity=payload.quantity,
                condition=payload.condition,
                location=payload.warehouse,
                captured_at=self._normalize_utc(payload.captured_at),
                notes=payload.notes,
                raw_payload=payload.raw_payload,
                source_document_id=source_document.id if source_document else None,
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
        """Maintain price history spans for the given offer."""

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

        captured_at = self._normalize_utc(offer.captured_at)

        if open_entry:
            open_entry.valid_from = self._normalize_utc(open_entry.valid_from)
            if open_entry.valid_to is not None:
                open_entry.valid_to = self._normalize_utc(open_entry.valid_to)
            if (
                open_entry.price == offer.price
                and open_entry.currency == offer.currency
                and captured_at >= open_entry.valid_from
            ):
                return

            if captured_at >= open_entry.valid_from:
                open_entry.valid_to = captured_at

        history_entry = models.PriceHistory(
            product_id=offer.product_id,
            vendor_id=offer.vendor_id,
            price=offer.price,
            currency=offer.currency,
            valid_from=captured_at,
            valid_to=open_entry.valid_from if open_entry and captured_at < open_entry.valid_from else None,
            source_offer_id=offer.id,
        )
        self.session.add(history_entry)

    @staticmethod
    def _normalize_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
