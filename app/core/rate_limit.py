from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import Dict


@dataclass
class _TokenBucket:
    capacity: float
    refill_rate: float  # tokens per second
    tokens: float = field(init=False)
    updated_at: float = field(default_factory=monotonic)

    def __post_init__(self) -> None:
        self.tokens = self.capacity

    def consume(self, requested: float) -> bool:
        now = monotonic()
        elapsed = now - self.updated_at
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.updated_at = now
        if self.tokens >= requested:
            self.tokens -= requested
            return True
        return False


class TokenBucketLimiter:
    """Simple in-memory token bucket limiter keyed by arbitrary strings."""

    def __init__(self, capacity: float, refill_rate: float) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be positive")
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._buckets: Dict[str, _TokenBucket] = {}
        self._lock = Lock()

    def allow(self, key: str, tokens: float = 1.0) -> bool:
        if tokens <= 0:
            return True
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _TokenBucket(capacity=self.capacity, refill_rate=self.refill_rate)
                self._buckets[key] = bucket
            return bucket.consume(tokens)
