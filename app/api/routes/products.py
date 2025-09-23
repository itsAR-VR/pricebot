from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import get_db
from app.db import models
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


class ProductDetail(ProductSummary):
    recent_offers: list[OfferOut]


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
    offer_count = session.exec(count_stmt).one()

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
