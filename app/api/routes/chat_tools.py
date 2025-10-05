from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlmodel import Session

from app.api.deps import get_db
from app.db import models
from app.services.chat import ChatLookupService

router = APIRouter(prefix="/chat/tools", tags=["chat"])


class ProductResolveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(5, ge=1, le=10)
    offset: int = Field(0, ge=0)

    @field_validator("query")
    @classmethod
    def _validate_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must contain non-whitespace characters")
        return normalized


class ProductCandidate(BaseModel):
    id: UUID
    canonical_name: str
    model_number: str | None = None
    upc: str | None = None
    match_source: str
    spec: dict[str, Any] | None = None


class ProductResolveResponse(BaseModel):
    products: list[ProductCandidate]
    limit: int
    offset: int
    total: int
    has_more: bool
    next_offset: int | None = None


class OfferSearchFilters(BaseModel):
    vendor_id: UUID | None = None
    condition: str | None = None
    location: str | None = None
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    captured_since: datetime | None = None

    @field_validator("condition")
    @classmethod
    def _normalize_condition(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("location")
    @classmethod
    def _normalize_location(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _validate_price_range(self) -> "OfferSearchFilters":
        if self.min_price is not None and self.max_price is not None:
            if self.min_price > self.max_price:
                raise ValueError("min_price cannot be greater than max_price")
        return self


class VendorSummary(BaseModel):
    id: UUID
    name: str
    contact_info: dict[str, Any] | None = None


class DocumentSummary(BaseModel):
    id: UUID
    file_name: str
    file_type: str
    status: str
    ingest_completed_at: datetime | None = None


class OfferSummary(BaseModel):
    id: UUID
    price: float
    currency: str
    captured_at: datetime
    quantity: int | None = None
    condition: str | None = None
    location: str | None = None
    vendor: VendorSummary
    source_document: DocumentSummary | None = None


class ProductDetail(BaseModel):
    id: UUID
    canonical_name: str
    model_number: str | None = None
    upc: str | None = None
    match_source: str
    image_url: str | None = None
    spec: dict[str, Any] | None = None


class BestPriceRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    filters: OfferSearchFilters = Field(default_factory=OfferSearchFilters)
    limit: int = Field(5, ge=1, le=10, description="Maximum offers per product to return")
    offset: int = Field(0, ge=0, description="Number of matched products to skip")

    @field_validator("query")
    @classmethod
    def _validate_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must contain non-whitespace characters")
        return normalized


class ProductOfferBundle(BaseModel):
    product: ProductDetail
    best_offer: OfferSummary | None
    alternate_offers: list[OfferSummary]


class BestPriceResponse(BaseModel):
    results: list[ProductOfferBundle]
    limit: int
    offset: int
    total: int
    has_more: bool
    next_offset: int | None = None
    applied_filters: OfferSearchFilters


def _extract_image_url(spec: dict[str, Any] | None) -> str | None:
    if not spec:
        return None
    for key in ("image_url", "photo_url", "image", "photo"):
        value = spec.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _serialize_offer(offer: models.Offer) -> OfferSummary:
    vendor_contact: dict[str, Any] | None = None
    if offer.vendor and offer.vendor.contact_info:
        vendor_contact = offer.vendor.contact_info

    vendor = VendorSummary(
        id=offer.vendor.id,
        name=offer.vendor.name,
        contact_info=vendor_contact,
    )

    document_summary: DocumentSummary | None = None
    if offer.source_document:
        document_summary = DocumentSummary(
            id=offer.source_document.id,
            file_name=offer.source_document.file_name,
            file_type=offer.source_document.file_type,
            status=offer.source_document.status,
            ingest_completed_at=offer.source_document.ingest_completed_at,
        )

    return OfferSummary(
        id=offer.id,
        price=offer.price,
        currency=offer.currency,
        captured_at=offer.captured_at,
        quantity=offer.quantity,
        condition=offer.condition,
        location=offer.location,
        vendor=vendor,
        source_document=document_summary,
    )


@router.post("/products/resolve", response_model=ProductResolveResponse)
def resolve_products(payload: ProductResolveRequest, session: Session = Depends(get_db)) -> ProductResolveResponse:
    service = ChatLookupService(session)
    result_page = service.resolve_products(
        payload.query,
        limit=payload.limit,
        offset=payload.offset,
        include_total=True,
    )

    products = [
        ProductCandidate(
            id=match.product.id,
            canonical_name=match.product.canonical_name,
            model_number=match.product.model_number,
            upc=match.product.upc,
            match_source=match.match_source,
            spec=match.product.spec or None,
        )
        for match in result_page.matches
    ]
    next_offset = payload.offset + len(result_page.matches) if result_page.has_more else None
    return ProductResolveResponse(
        products=products,
        limit=payload.limit,
        offset=payload.offset,
        total=result_page.total,
        has_more=result_page.has_more,
        next_offset=next_offset,
    )


@router.post("/offers/search-best-price", response_model=BestPriceResponse)
def search_best_price(payload: BestPriceRequest, session: Session = Depends(get_db)) -> BestPriceResponse:
    service = ChatLookupService(session)

    if payload.filters.vendor_id:
        vendor = session.get(models.Vendor, payload.filters.vendor_id)
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")

    result_page = service.resolve_products(
        payload.query,
        limit=payload.limit,
        offset=payload.offset,
        include_total=True,
    )

    if not result_page.matches:
        return BestPriceResponse(
            results=[],
            limit=payload.limit,
            offset=payload.offset,
            total=result_page.total,
            has_more=False,
            next_offset=None,
            applied_filters=payload.filters,
        )

    product_ids = [match.product.id for match in result_page.matches]
    bundles = service.fetch_best_offers(
        product_ids,
        vendor_id=payload.filters.vendor_id,
        condition=payload.filters.condition,
        location=payload.filters.location,
        max_offers=payload.limit,
        min_price=payload.filters.min_price,
        max_price=payload.filters.max_price,
        captured_since=payload.filters.captured_since,
    )
    bundle_map = {bundle.product.id: bundle for bundle in bundles}

    results: list[ProductOfferBundle] = []
    for match in result_page.matches:
        bundle = bundle_map.get(match.product.id)
        offers = bundle.offers if bundle else []
        best_offer = _serialize_offer(offers[0]) if offers else None
        alternate_offers = [_serialize_offer(offer) for offer in offers[1:]] if offers else []

        product_spec = match.product.spec if isinstance(match.product.spec, dict) else None
        product_detail = ProductDetail(
            id=match.product.id,
            canonical_name=match.product.canonical_name,
            model_number=match.product.model_number,
            upc=match.product.upc,
            match_source=match.match_source,
            image_url=_extract_image_url(product_spec),
            spec=product_spec,
        )

        results.append(
            ProductOfferBundle(
                product=product_detail,
                best_offer=best_offer,
                alternate_offers=alternate_offers,
            )
        )
    next_offset = payload.offset + len(result_page.matches) if result_page.has_more else None
    return BestPriceResponse(
        results=results,
        limit=payload.limit,
        offset=payload.offset,
        total=result_page.total,
        has_more=result_page.has_more,
        next_offset=next_offset,
        applied_filters=payload.filters,
    )


__all__ = [
    "router",
]
