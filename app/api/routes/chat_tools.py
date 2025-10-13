from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.api.deps import get_db
from app.api.routes.health import healthcheck as health_status
from app.core.config import settings
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


class RecentProductSuggestionOut(BaseModel):
    id: UUID
    canonical_name: str
    alias: str | None = None
    last_seen: datetime | None = None
    offer_count: int = 0


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
    recent_products: list[RecentProductSuggestionOut] = Field(default_factory=list)


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
        recent_products = [
            RecentProductSuggestionOut(
                id=suggestion.product_id,
                canonical_name=suggestion.canonical_name,
                alias=suggestion.alias,
                last_seen=suggestion.last_seen,
                offer_count=suggestion.offer_count,
            )
            for suggestion in service.fetch_recent_product_summaries(limit=5)
        ]
        return BestPriceResponse(
            results=[],
            limit=payload.limit,
            offset=payload.offset,
            total=result_page.total,
            has_more=False,
            next_offset=None,
            applied_filters=payload.filters,
            recent_products=recent_products,
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
        recent_products=[],
    )


class DiagnosticsCounts(BaseModel):
    vendors: int
    products: int
    offers: int
    documents: int


class DiagnosticsDocument(BaseModel):
    id: UUID
    file_name: str
    status: str
    offers_count: int
    ingest_started_at: datetime | None = None
    ingest_completed_at: datetime | None = None
    ingestion_errors: list[str] = Field(default_factory=list)


class DiagnosticsOffer(BaseModel):
    id: UUID
    product_name: str | None
    vendor_name: str | None
    price: float
    currency: str
    captured_at: datetime
    quantity: int | None = None
    condition: str | None = None


class DiagnosticsFeatureFlags(BaseModel):
    enable_openai: bool
    default_currency: str
    environment: str


class DiagnosticsIngestionWarning(BaseModel):
    document_id: UUID
    file_name: str
    messages: list[str]


class DiagnosticsResponse(BaseModel):
    metadata: dict[str, str]
    health: dict[str, Any]
    counts: DiagnosticsCounts
    recent_documents: list[DiagnosticsDocument]
    recent_offers: list[DiagnosticsOffer]
    feature_flags: DiagnosticsFeatureFlags
    ingestion_warnings: list[DiagnosticsIngestionWarning]


def _coerce_messages(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw if item]
    if isinstance(raw, dict):
        return [f"{key}: {value}" for key, value in raw.items()]
    return [str(raw)]


def _row_count(session: Session, model: type[Any]) -> int:
    count_statement = select(func.count()).select_from(model)
    return int(session.exec(count_statement).one())


def _collect_diagnostics(session: Session) -> DiagnosticsResponse:
    metadata = {"service": settings.app_name, "environment": settings.environment}
    health = health_status()

    counts = DiagnosticsCounts(
        vendors=_row_count(session, models.Vendor),
        products=_row_count(session, models.Product),
        offers=_row_count(session, models.Offer),
        documents=_row_count(session, models.SourceDocument),
    )

    document_order = func.coalesce(
        models.SourceDocument.ingest_completed_at,
        models.SourceDocument.ingest_started_at,
    )
    document_stmt = (
        select(models.SourceDocument, func.count(models.Offer.id))
        .outerjoin(models.Offer, models.Offer.source_document_id == models.SourceDocument.id)
        .group_by(models.SourceDocument.id)
        .order_by(document_order.desc(), models.SourceDocument.file_name)
        .limit(10)
    )
    document_rows = session.exec(document_stmt).all()

    recent_documents: list[DiagnosticsDocument] = []
    ingestion_warnings: list[DiagnosticsIngestionWarning] = []
    for doc, offer_count in document_rows:
        ingestion_errors = _coerce_messages(
            (doc.extra or {}).get("ingestion_errors") if isinstance(doc.extra, dict) else None
        )
        recent_documents.append(
            DiagnosticsDocument(
                id=doc.id,
                file_name=doc.file_name,
                status=doc.status,
                offers_count=int(offer_count or 0),
                ingest_started_at=doc.ingest_started_at,
                ingest_completed_at=doc.ingest_completed_at,
                ingestion_errors=ingestion_errors,
            )
        )
        if ingestion_errors:
            ingestion_warnings.append(
                DiagnosticsIngestionWarning(
                    document_id=doc.id,
                    file_name=doc.file_name,
                    messages=ingestion_errors,
                )
            )

    offer_stmt = (
        select(models.Offer)
        .options(
            selectinload(models.Offer.product),
            selectinload(models.Offer.vendor),
        )
        .order_by(models.Offer.captured_at.desc())
        .limit(10)
    )
    offers = session.exec(offer_stmt).all()

    recent_offers = [
        DiagnosticsOffer(
            id=offer.id,
            product_name=offer.product.canonical_name if offer.product else None,
            vendor_name=offer.vendor.name if offer.vendor else None,
            price=offer.price,
            currency=offer.currency,
            captured_at=offer.captured_at,
            quantity=offer.quantity,
            condition=offer.condition,
        )
        for offer in offers
    ]

    feature_flags = DiagnosticsFeatureFlags(
        enable_openai=settings.enable_openai,
        default_currency=settings.default_currency,
        environment=settings.environment,
    )

    return DiagnosticsResponse(
        metadata=metadata,
        health=health,
        counts=counts,
        recent_documents=recent_documents,
        recent_offers=recent_offers,
        feature_flags=feature_flags,
        ingestion_warnings=ingestion_warnings,
    )


@router.get("/diagnostics", response_model=DiagnosticsResponse)
def get_diagnostics(session: Session = Depends(get_db)) -> DiagnosticsResponse:
    return _collect_diagnostics(session)


@router.get("/diagnostics/download", include_in_schema=False)
def download_diagnostics(session: Session = Depends(get_db)) -> JSONResponse:
    diagnostics = _collect_diagnostics(session)
    payload = diagnostics.model_dump(mode="json")
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": 'attachment; filename="pricebot_diagnostics.json"'},
    )


__all__ = [
    "router",
]
