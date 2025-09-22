from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def now_utc() -> datetime:
    return datetime.utcnow()


@dataclass(slots=True)
class RawOffer:
    """Represents a normalized offer row prior to persistence."""

    vendor_name: str
    product_name: str
    price: float
    currency: str = "USD"
    quantity: int | None = None
    condition: str | None = None
    sku: str | None = None
    upc: str | None = None
    model_number: str | None = None
    warehouse: str | None = None
    captured_at: datetime = field(default_factory=now_utc)
    notes: str | None = None
    raw_payload: dict[str, Any] | None = None


@dataclass(slots=True)
class IngestionResult:
    offers: list[RawOffer]
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.errors
