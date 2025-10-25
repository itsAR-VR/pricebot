from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def now_utc() -> datetime:
    """Return a timezone-naive UTC datetime."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class RawOffer:
    """Represents a normalized offer row prior to persistence."""

    vendor_name: str
    product_name: str
    price: float
    currency: str = "USD"
    quantity: Optional[int] = None
    condition: Optional[str] = None
    sku: Optional[str] = None
    upc: Optional[str] = None
    model_number: Optional[str] = None
    warehouse: Optional[str] = None
    captured_at: datetime = field(default_factory=now_utc)
    notes: Optional[str] = None
    raw_payload: Optional[Dict[str, Any]] = None


@dataclass
class IngestionResult:
    offers: List[RawOffer]
    errors: List[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.errors
