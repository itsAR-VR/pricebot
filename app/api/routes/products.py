from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import get_db
from app.db import models
from app.services.chat import ChatLookupService
from app.api.routes.offers import OfferOut

router = APIRouter(prefix="/products", tags=["products"])


class ProductSummary(BaseModel):
    id: UUID
    canonical_name: str
    brand: str | None = None
    model_number: str | None = None
    upc: str | None = None
    category: str | None = None
    offer_count: int


class ProductSuggestItem(BaseModel):
    id: UUID
    canonical_name: str
    model_number: str | None = None
    match_source: str


class ProductDetail(ProductSummary):
    recent_offers: list[OfferOut]


@router.get("/suggest", response_model=list[ProductSuggestItem], summary="Suggest products for mentions")
def suggest_products(
    q: str = Query(
        ...,
        min_length=1,
        max_length=200,
        description="Search term for product suggestions",
    ),
    limit: int = Query(default=8, ge=1, le=25),
    session: Session = Depends(get_db),
) -> list[ProductSuggestItem]:
    normalized_query = q.strip()
    if not normalized_query:
        raise HTTPException(status_code=422, detail="Query must not be blank")

    service = ChatLookupService(session)
    result_page = service.resolve_products(
        normalized_query,
        limit=limit,
        offset=0,
        include_total=False,
    )

    suggestions: list[ProductSuggestItem] = []
    for match in result_page.matches:
        suggestions.append(
            ProductSuggestItem(
                id=match.product.id,
                canonical_name=match.product.canonical_name,
                model_number=match.product.model_number,
                match_source=match.match_source,
            )
        )

    return suggestions


@router.get("", response_model=list[ProductSummary], summary="List products")
def list_products(
    session: Session = Depends(get_db),
    q: Optional[str] = Query(default=None, description="Search by name, model, or UPC"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ProductSummary]:
    statement = select(models.Product)
    if q:
        pattern = f"%{q.lower()}%"
        statement = statement.where(
            func.lower(models.Product.canonical_name).like(pattern)
            | func.lower(models.Product.model_number).like(pattern)
            | func.lower(models.Product.upc).like(pattern)
        )
    statement = statement.order_by(models.Product.canonical_name).offset(offset).limit(limit)
    products = session.exec(statement).all()

    if not products:
        return []

    product_ids = [product.id for product in products]
    count_stmt = (
        select(models.Offer.product_id, func.count(models.Offer.id))
        .where(models.Offer.product_id.in_(product_ids))
        .group_by(models.Offer.product_id)
    )
    counts = {product_id: count for product_id, count in session.exec(count_stmt).all()}

    return [
        ProductSummary(
            id=product.id,
            canonical_name=product.canonical_name,
            brand=product.brand,
            model_number=product.model_number,
            upc=product.upc,
            category=product.category,
            offer_count=counts.get(product.id, 0),
        )
        for product in products
    ]


@router.get("/{product_id}", response_model=ProductDetail, summary="Get product detail")
def get_product(
    product_id: UUID,
    session: Session = Depends(get_db),
    offer_limit: int = Query(default=20, ge=1, le=200),
) -> ProductDetail:
    product = session.get(models.Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    offer_stmt = (
        select(models.Offer)
        .where(models.Offer.product_id == product_id)
        .order_by(models.Offer.captured_at.desc())
        .limit(offer_limit)
    )
    offers = session.exec(offer_stmt).all()

    offer_outputs = [
        OfferOut(
            id=offer.id,
            product_id=offer.product_id,
            vendor_id=offer.vendor_id,
            product_name=offer.product.canonical_name if offer.product else "Unknown",
            vendor_name=offer.vendor.name if offer.vendor else "Unknown",
            price=offer.price,
            currency=offer.currency,
            captured_at=offer.captured_at,
            condition=offer.condition,
            quantity=offer.quantity,
            location=offer.location,
        )
        for offer in offers
    ]

    count_stmt = (
        select(func.count(models.Offer.id))
        .where(models.Offer.product_id == product_id)
    )
    offer_count = int(session.exec(count_stmt).one())

    return ProductDetail(
        id=product.id,
        canonical_name=product.canonical_name,
        brand=product.brand,
        model_number=product.model_number,
        upc=product.upc,
        category=product.category,
        offer_count=offer_count,
        recent_offers=offer_outputs,
    )


# ------------------------------------------------------------------
# Product Alias Management (P1)
# ------------------------------------------------------------------


class AliasOut(BaseModel):
    """Product alias response model."""

    id: UUID
    product_id: UUID
    alias_text: str
    source_vendor_id: UUID | None = None
    source_vendor_name: str | None = None
    has_embedding: bool = False


class AliasCreate(BaseModel):
    """Request to create a product alias."""

    alias_text: str = Field(..., min_length=1, max_length=500, description="Alias text for the product")
    source_vendor_id: UUID | None = Field(default=None, description="Vendor this alias came from")


class AliasUpdate(BaseModel):
    """Request to update a product alias."""

    alias_text: str | None = Field(default=None, min_length=1, max_length=500)
    source_vendor_id: UUID | None = None


class AliasBulkCreate(BaseModel):
    """Request to bulk create aliases for a product."""

    aliases: list[AliasCreate] = Field(..., min_length=1, max_length=100)


@router.get(
    "/{product_id}/aliases",
    response_model=list[AliasOut],
    summary="List aliases for a product",
)
def list_product_aliases(
    product_id: UUID,
    session: Session = Depends(get_db),
) -> list[AliasOut]:
    """Get all aliases associated with a product."""
    product = session.get(models.Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    stmt = select(models.ProductAlias).where(models.ProductAlias.product_id == product_id)
    aliases = session.exec(stmt).all()

    return [
        AliasOut(
            id=alias.id,
            product_id=alias.product_id,
            alias_text=alias.alias_text,
            source_vendor_id=alias.source_vendor_id,
            source_vendor_name=alias.source_vendor.name if alias.source_vendor else None,
            has_embedding=alias.embedding is not None,
        )
        for alias in aliases
    ]


@router.post(
    "/{product_id}/aliases",
    response_model=AliasOut,
    status_code=201,
    summary="Create an alias for a product",
)
def create_product_alias(
    product_id: UUID,
    payload: AliasCreate,
    session: Session = Depends(get_db),
) -> AliasOut:
    """Create a new alias for a product."""
    product = session.get(models.Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Validate source vendor if provided
    source_vendor = None
    if payload.source_vendor_id:
        source_vendor = session.get(models.Vendor, payload.source_vendor_id)
        if not source_vendor:
            raise HTTPException(status_code=404, detail="Source vendor not found")

    # Check for duplicate alias
    existing = session.exec(
        select(models.ProductAlias).where(
            models.ProductAlias.product_id == product_id,
            models.ProductAlias.alias_text == payload.alias_text,
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Alias already exists for this product")

    alias = models.ProductAlias(
        product_id=product_id,
        alias_text=payload.alias_text.strip(),
        source_vendor_id=payload.source_vendor_id,
    )
    session.add(alias)
    session.commit()
    session.refresh(alias)

    return AliasOut(
        id=alias.id,
        product_id=alias.product_id,
        alias_text=alias.alias_text,
        source_vendor_id=alias.source_vendor_id,
        source_vendor_name=source_vendor.name if source_vendor else None,
        has_embedding=alias.embedding is not None,
    )


@router.post(
    "/{product_id}/aliases/bulk",
    response_model=list[AliasOut],
    status_code=201,
    summary="Bulk create aliases for a product",
)
def bulk_create_product_aliases(
    product_id: UUID,
    payload: AliasBulkCreate,
    session: Session = Depends(get_db),
) -> list[AliasOut]:
    """Create multiple aliases for a product at once."""
    product = session.get(models.Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    created: list[AliasOut] = []
    for alias_req in payload.aliases:
        # Skip duplicates silently
        existing = session.exec(
            select(models.ProductAlias).where(
                models.ProductAlias.product_id == product_id,
                models.ProductAlias.alias_text == alias_req.alias_text,
            )
        ).first()
        if existing:
            continue

        source_vendor = None
        if alias_req.source_vendor_id:
            source_vendor = session.get(models.Vendor, alias_req.source_vendor_id)

        alias = models.ProductAlias(
            product_id=product_id,
            alias_text=alias_req.alias_text.strip(),
            source_vendor_id=alias_req.source_vendor_id,
        )
        session.add(alias)
        session.flush()

        created.append(
            AliasOut(
                id=alias.id,
                product_id=alias.product_id,
                alias_text=alias.alias_text,
                source_vendor_id=alias.source_vendor_id,
                source_vendor_name=source_vendor.name if source_vendor else None,
                has_embedding=False,
            )
        )

    session.commit()
    return created


@router.put(
    "/{product_id}/aliases/{alias_id}",
    response_model=AliasOut,
    summary="Update a product alias",
)
def update_product_alias(
    product_id: UUID,
    alias_id: UUID,
    payload: AliasUpdate,
    session: Session = Depends(get_db),
) -> AliasOut:
    """Update an existing product alias."""
    alias = session.get(models.ProductAlias, alias_id)
    if not alias or alias.product_id != product_id:
        raise HTTPException(status_code=404, detail="Alias not found")

    if payload.alias_text is not None:
        # Check for duplicate if text is changing
        if payload.alias_text != alias.alias_text:
            existing = session.exec(
                select(models.ProductAlias).where(
                    models.ProductAlias.product_id == product_id,
                    models.ProductAlias.alias_text == payload.alias_text,
                    models.ProductAlias.id != alias_id,
                )
            ).first()
            if existing:
                raise HTTPException(status_code=409, detail="Alias already exists for this product")
        alias.alias_text = payload.alias_text.strip()
        # Clear embedding when text changes
        alias.embedding = None

    if payload.source_vendor_id is not None:
        if payload.source_vendor_id:
            source_vendor = session.get(models.Vendor, payload.source_vendor_id)
            if not source_vendor:
                raise HTTPException(status_code=404, detail="Source vendor not found")
        alias.source_vendor_id = payload.source_vendor_id

    session.add(alias)
    session.commit()
    session.refresh(alias)

    return AliasOut(
        id=alias.id,
        product_id=alias.product_id,
        alias_text=alias.alias_text,
        source_vendor_id=alias.source_vendor_id,
        source_vendor_name=alias.source_vendor.name if alias.source_vendor else None,
        has_embedding=alias.embedding is not None,
    )


@router.delete(
    "/{product_id}/aliases/{alias_id}",
    status_code=204,
    summary="Delete a product alias",
)
def delete_product_alias(
    product_id: UUID,
    alias_id: UUID,
    session: Session = Depends(get_db),
) -> None:
    """Delete a product alias."""
    alias = session.get(models.ProductAlias, alias_id)
    if not alias or alias.product_id != product_id:
        raise HTTPException(status_code=404, detail="Alias not found")

    session.delete(alias)
    session.commit()


# Global aliases list endpoint
@router.get(
    "/aliases/all",
    response_model=list[AliasOut],
    summary="List all product aliases",
    tags=["aliases"],
)
def list_all_aliases(
    session: Session = Depends(get_db),
    q: str | None = Query(default=None, description="Search by alias text"),
    vendor_id: UUID | None = Query(default=None, description="Filter by source vendor"),
    has_embedding: bool | None = Query(default=None, description="Filter by embedding status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[AliasOut]:
    """List all product aliases with optional filters."""
    stmt = select(models.ProductAlias)

    if q:
        pattern = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(models.ProductAlias.alias_text).like(pattern))

    if vendor_id:
        stmt = stmt.where(models.ProductAlias.source_vendor_id == vendor_id)

    if has_embedding is not None:
        if has_embedding:
            stmt = stmt.where(models.ProductAlias.embedding.isnot(None))
        else:
            stmt = stmt.where(models.ProductAlias.embedding.is_(None))

    stmt = stmt.order_by(models.ProductAlias.alias_text).offset(offset).limit(limit)
    aliases = session.exec(stmt).all()

    return [
        AliasOut(
            id=alias.id,
            product_id=alias.product_id,
            alias_text=alias.alias_text,
            source_vendor_id=alias.source_vendor_id,
            source_vendor_name=alias.source_vendor.name if alias.source_vendor else None,
            has_embedding=alias.embedding is not None,
        )
        for alias in aliases
    ]
