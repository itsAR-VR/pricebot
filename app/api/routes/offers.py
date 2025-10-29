from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_db
from app.db import models

router = APIRouter(prefix="/offers", tags=["offers"])


class OfferOut(BaseModel):
    id: UUID
    product_id: UUID
    vendor_id: UUID
    product_name: str
    vendor_name: str
    price: float
    currency: str
    captured_at: datetime
    condition: str | None = None
    quantity: int | None = None
    location: str | None = None


@router.get(
    "",
    response_model=list[OfferOut],
    summary="List recent offers with optional filters",
)
def list_offers(
    limit: int = Query(default=50, ge=1, le=500),
    product_id: Optional[UUID] = None,
    vendor_id: Optional[UUID] = None,
    since: Optional[datetime] = None,
    session: Session = Depends(get_db),
) -> list[OfferOut]:
    statement = select(models.Offer)
    if product_id:
        statement = statement.where(models.Offer.product_id == product_id)
    if vendor_id:
        statement = statement.where(models.Offer.vendor_id == vendor_id)
    if since:
        statement = statement.where(models.Offer.captured_at >= since)

    statement = statement.order_by(models.Offer.captured_at.desc()).limit(limit)
    offers = session.exec(statement).all()
    response: list[OfferOut] = []
    for offer in offers:
        response.append(
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
        )
    return response
