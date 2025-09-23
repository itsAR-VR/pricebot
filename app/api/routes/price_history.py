from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_db
from app.db import models

router = APIRouter(prefix="/price-history", tags=["price-history"])


class PriceHistoryOut(BaseModel):
    id: UUID
    product_id: UUID
    vendor_id: UUID
    price: float
    currency: str
    valid_from: datetime
    valid_to: datetime | None = None
    source_offer_id: UUID


@router.get("/product/{product_id}", response_model=list[PriceHistoryOut], summary="Price history for a product")
def product_history(
    product_id: UUID,
    session: Session = Depends(get_db),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[PriceHistoryOut]:
    statement = (
        select(models.PriceHistory)
        .where(models.PriceHistory.product_id == product_id)
        .order_by(models.PriceHistory.valid_from.desc())
        .limit(limit)
    )
    entries = session.exec(statement).all()
    return [
        PriceHistoryOut(
            id=entry.id,
            product_id=entry.product_id,
            vendor_id=entry.vendor_id,
            price=entry.price,
            currency=entry.currency,
            valid_from=entry.valid_from,
            valid_to=entry.valid_to,
            source_offer_id=entry.source_offer_id,
        )
        for entry in entries
    ]


@router.get("/vendor/{vendor_id}", response_model=list[PriceHistoryOut], summary="Price history for a vendor")
def vendor_history(
    vendor_id: UUID,
    session: Session = Depends(get_db),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[PriceHistoryOut]:
    statement = (
        select(models.PriceHistory)
        .where(models.PriceHistory.vendor_id == vendor_id)
        .order_by(models.PriceHistory.valid_from.desc())
        .limit(limit)
    )
    entries = session.exec(statement).all()
    return [
        PriceHistoryOut(
            id=entry.id,
            product_id=entry.product_id,
            vendor_id=entry.vendor_id,
            price=entry.price,
            currency=entry.currency,
            valid_from=entry.valid_from,
            valid_to=entry.valid_to,
            source_offer_id=entry.source_offer_id,
        )
        for entry in entries
    ]
