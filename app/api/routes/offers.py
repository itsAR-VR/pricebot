from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_db
from app.db import models

router = APIRouter(prefix="/offers", tags=["offers"])


class OfferOut(BaseModel):
    id: UUID
    product_name: str
    vendor_name: str
    price: float
    currency: str
    captured_at: datetime
    condition: str | None = None
    quantity: int | None = None
    location: str | None = None


@router.get("", response_model=list[OfferOut], summary="List recent offers")
def list_offers(limit: int = 50, session: Session = Depends(get_db)) -> list[OfferOut]:
    statement = select(models.Offer).order_by(models.Offer.captured_at.desc()).limit(limit)
    offers = session.exec(statement).all()
    response: list[OfferOut] = []
    for offer in offers:
        response.append(
            OfferOut(
                id=offer.id,
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
