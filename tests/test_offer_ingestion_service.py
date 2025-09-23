from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.ingestion.types import RawOffer
from app.services.offers import OfferIngestionService
from app.db import models


def _raw_offer(name: str, vendor: str, price: float, captured_at: datetime) -> RawOffer:
    return RawOffer(
        product_name=name,
        vendor_name=vendor,
        price=price,
        quantity=5,
        captured_at=captured_at,
    )


def test_price_history_created_for_first_offer(session) -> None:
    service = OfferIngestionService(session)
    captured_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    service.ingest([_raw_offer("Widget", "VendorA", 100.0, captured_at)])
    session.commit()

    offers = session.exec(select(models.Offer)).all()
    history = session.exec(select(models.PriceHistory)).all()

    assert len(offers) == 1
    assert len(history) == 1
    entry = history[0]
    assert entry.valid_from == captured_at.replace(tzinfo=None)
    assert entry.valid_to is None
    assert entry.price == 100.0


def test_price_history_not_duplicated_for_identical_offer(session) -> None:
    service = OfferIngestionService(session)
    captured_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    later = captured_at + timedelta(days=1)

    service.ingest([_raw_offer("Widget", "VendorA", 100.0, captured_at)])
    service.ingest([_raw_offer("Widget", "VendorA", 100.0, later)])
    session.commit()

    history = session.exec(select(models.PriceHistory)).all()
    assert len(history) == 1
    assert history[0].valid_to is None


def test_price_history_closes_prior_span_on_price_change(session) -> None:
    service = OfferIngestionService(session)
    t0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)
    t2 = t1 + timedelta(days=1)

    service.ingest([_raw_offer("Widget", "VendorA", 100.0, t0)])
    service.ingest([_raw_offer("Widget", "VendorA", 110.0, t1)])
    service.ingest([_raw_offer("Widget", "VendorA", 120.0, t2)])
    session.commit()

    history = session.exec(select(models.PriceHistory).order_by(models.PriceHistory.valid_from)).all()

    assert len(history) == 3
    first, second, third = history
    assert first.price == 100.0
    assert first.valid_to == t1.replace(tzinfo=None)
    assert second.price == 110.0
    assert second.valid_to == t2.replace(tzinfo=None)
    assert third.price == 120.0
    assert third.valid_to is None
