from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import get_db
from app.api.routes.offers import OfferOut
from app.db import models

router = APIRouter(prefix="/vendors", tags=["vendors"])


class VendorSummary(BaseModel):
    id: UUID
    name: str
    offer_count: int


class VendorDetail(VendorSummary):
    recent_offers: list[OfferOut]


@router.get("", response_model=list[VendorSummary], summary="List vendors")
def list_vendors(
    session: Session = Depends(get_db),
    q: Optional[str] = Query(default=None, description="Search by vendor name"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[VendorSummary]:
    statement = select(models.Vendor)
    if q:
        pattern = f"%{q.lower()}%"
        statement = statement.where(func.lower(models.Vendor.name).like(pattern))

    statement = statement.order_by(models.Vendor.name).offset(offset).limit(limit)
    vendors = session.exec(statement).all()

    if not vendors:
        return []

    vendor_ids = [vendor.id for vendor in vendors]
    count_stmt = (
        select(models.Offer.vendor_id, func.count(models.Offer.id))
        .where(models.Offer.vendor_id.in_(vendor_ids))
        .group_by(models.Offer.vendor_id)
    )
    counts = {vendor_id: count for vendor_id, count in session.exec(count_stmt).all()}

    return [
        VendorSummary(
            id=vendor.id,
            name=vendor.name,
            offer_count=counts.get(vendor.id, 0),
        )
        for vendor in vendors
    ]


@router.get("/{vendor_id}", response_model=VendorDetail, summary="Get vendor detail")
def get_vendor(
    vendor_id: UUID,
    session: Session = Depends(get_db),
    offer_limit: int = Query(default=20, ge=1, le=200),
) -> VendorDetail:
    vendor = session.get(models.Vendor, vendor_id)
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    offer_stmt = (
        select(models.Offer)
        .where(models.Offer.vendor_id == vendor_id)
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
            vendor_name=vendor.name,
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
        .where(models.Offer.vendor_id == vendor_id)
    )
    offer_count = session.exec(count_stmt).one()

    return VendorDetail(
        id=vendor.id,
        name=vendor.name,
        offer_count=offer_count,
        recent_offers=offer_outputs,
    )
