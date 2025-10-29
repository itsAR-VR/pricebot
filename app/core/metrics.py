from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import DefaultDict, Dict, Iterable, List, Literal


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class WhatsAppMetricCounter:
    client_id: str
    chat_id: str
    chat_title: str | None = None
    accepted: int = 0
    created: int = 0
    deduped: int = 0
    extracted: int = 0
    errors: int = 0
    media_uploaded: int = 0
    media_deduped: int = 0
    media_failed: int = 0
    http_4xx: int = 0
    http_5xx: int = 0
    auth_failures: int = 0
    forbidden: int = 0
    rate_limited: int = 0
    signature_failures: int = 0
    last_event_at: datetime = field(default_factory=_utcnow)
    last_failure_status: int | None = None
    last_failure_reason: str | None = None
    last_failure_at: datetime | None = None

    def bump(
        self,
        *,
        accepted: int = 0,
        created: int = 0,
        deduped: int = 0,
        extracted: int = 0,
        errors: int = 0,
        media_uploaded: int = 0,
        media_deduped: int = 0,
        media_failed: int = 0,
        http_4xx: int = 0,
        http_5xx: int = 0,
        auth_failures: int = 0,
        forbidden: int = 0,
        rate_limited: int = 0,
        signature_failures: int = 0,
        failure_status: int | None = None,
        failure_reason: str | None = None,
        chat_title: str | None = None,
    ) -> None:
        self.accepted += accepted
        self.created += created
        self.deduped += deduped
        self.extracted += extracted
        self.errors += errors
        self.media_uploaded += media_uploaded
        self.media_deduped += media_deduped
        self.media_failed += media_failed
        self.http_4xx += http_4xx
        self.http_5xx += http_5xx
        self.auth_failures += auth_failures
        self.forbidden += forbidden
        self.rate_limited += rate_limited
        self.signature_failures += signature_failures
        if chat_title:
            self.chat_title = chat_title
        self.last_event_at = _utcnow()
        if failure_status or failure_reason:
            self.last_failure_status = failure_status
            self.last_failure_reason = failure_reason
            self.last_failure_at = _utcnow()


@dataclass(frozen=True)
class WhatsAppFailureEvent:
    timestamp: datetime
    client_id: str
    status_code: int
    reason: str | None = None
    chat_id: str | None = None
    chat_title: str | None = None


@dataclass(frozen=True)
class WhatsAppMediaFailureEvent:
    timestamp: datetime
    client_id: str
    chat_id: str | None
    chat_title: str | None
    reason: str | None


class WhatsAppMetrics:
    """In-memory counters for WhatsApp ingest and extraction activity."""

    def __init__(self) -> None:
        self._counters: Dict[tuple[str, str], WhatsAppMetricCounter] = {}
        self._lock = Lock()
        self._recent_failures: deque[WhatsAppFailureEvent] = deque(maxlen=50)
        self._media_failures: deque[WhatsAppMediaFailureEvent] = deque(maxlen=50)

    def _identify(self, client_id: str | None, chat_id: str | None) -> tuple[str, str]:
        normalized_client = client_id or "unknown"
        normalized_chat = chat_id or "unknown"
        return normalized_client, normalized_chat

    def record_ingest(
        self,
        *,
        client_id: str | None,
        decisions: Iterable[dict],
    ) -> None:
        accepted_by_chat: DefaultDict[str | None, List[dict]] = defaultdict(list)
        for entry in decisions:
            accepted_by_chat[entry.get("chat_id")].append(entry)

        with self._lock:
            for chat_id, entries in accepted_by_chat.items():
                key = self._identify(client_id, chat_id)
                counter = self._counters.get(key)
                if counter is None:
                    counter = WhatsAppMetricCounter(client_id=key[0], chat_id=key[1])
                    self._counters[key] = counter
                created = sum(1 for e in entries if e.get("status") == "created")
                deduped = sum(1 for e in entries if e.get("status") == "deduped")
                errors = sum(1 for e in entries if e.get("status") == "skipped")
                counter.bump(
                    accepted=len(entries),
                    created=created,
                    deduped=deduped,
                    errors=errors,
                    chat_title=entries[0].get("chat_title"),
                )

    def record_extract(
        self,
        *,
        client_id: str | None,
        chat_id: str | None,
        chat_title: str | None,
        offers: int,
        errors: int = 0,
    ) -> None:
        key = self._identify(client_id, chat_id)
        with self._lock:
            counter = self._counters.get(key)
            if counter is None:
                counter = WhatsAppMetricCounter(client_id=key[0], chat_id=key[1])
                self._counters[key] = counter
            counter.bump(
                extracted=offers,
                errors=errors,
                chat_title=chat_title,
            )

    def record_media_upload(
        self,
        *,
        client_id: str | None,
        chat_id: str | None,
        chat_title: str | None,
        status: Literal["queued", "deduped", "failed"],
        reason: str | None = None,
    ) -> None:
        key = self._identify(client_id, chat_id)
        with self._lock:
            counter = self._counters.get(key)
            if counter is None:
                counter = WhatsAppMetricCounter(client_id=key[0], chat_id=key[1])
                self._counters[key] = counter
            if status == "queued":
                counter.bump(media_uploaded=1, chat_title=chat_title)
            elif status == "deduped":
                counter.bump(media_deduped=1, chat_title=chat_title)
            else:
                counter.bump(media_failed=1, errors=1, chat_title=chat_title, failure_reason=reason)
            if reason:
                # Optionally store last error reason for observability
                extra = counter.__dict__.setdefault("media_notes", [])
                if isinstance(extra, list) and reason not in extra:
                    extra.append(reason)
            if status == "failed":
                event = WhatsAppMediaFailureEvent(
                    timestamp=_utcnow(),
                    client_id=counter.client_id,
                    chat_id=None if key[1] == "unknown" else key[1],
                    chat_title=chat_title or counter.chat_title,
                    reason=reason,
                )
                self._media_failures.append(event)

    def snapshot(self) -> list[WhatsAppMetricCounter]:
        with self._lock:
            return sorted(self._counters.values(), key=lambda c: c.last_event_at, reverse=True)

    def record_http_event(
        self,
        *,
        client_id: str | None,
        chat_id: str | None = None,
        chat_title: str | None = None,
        status_code: int,
        reason: str | None = None,
    ) -> None:
        key = self._identify(client_id, chat_id)
        http_4xx = 1 if 400 <= status_code < 500 else 0
        http_5xx = 1 if 500 <= status_code < 600 else 0
        auth_failures = 1 if status_code == 401 else 0
        forbidden = 1 if status_code == 403 else 0
        rate_limited = 1 if status_code == 429 else 0
        signature_failures = 1 if reason in {"invalid_signature", "stale_signature"} else 0

        with self._lock:
            counter = self._counters.get(key)
            if counter is None:
                counter = WhatsAppMetricCounter(client_id=key[0], chat_id=key[1])
                self._counters[key] = counter
            counter.bump(
                http_4xx=http_4xx,
                http_5xx=http_5xx,
                auth_failures=auth_failures,
                forbidden=forbidden,
                rate_limited=rate_limited,
                signature_failures=signature_failures,
                failure_status=status_code,
                failure_reason=reason,
                chat_title=chat_title,
            )
            if reason:
                notes = counter.__dict__.setdefault("http_notes", [])
                if isinstance(notes, list):
                    if reason not in notes:
                        notes.append(reason)
                    while len(notes) > 10:
                        notes.pop(0)
            event = WhatsAppFailureEvent(
                timestamp=_utcnow(),
                client_id=counter.client_id,
                status_code=status_code,
                reason=reason,
                chat_id=counter.chat_id,
                chat_title=chat_title or counter.chat_title,
            )
            self._recent_failures.append(event)

    def recent_failures(self, limit: int = 10) -> List[WhatsAppFailureEvent]:
        with self._lock:
            events = list(self._recent_failures)
        if not events:
            return []
        return list(reversed(events[-limit:]))

    def recent_media_failures(self, limit: int = 10) -> List[WhatsAppMediaFailureEvent]:
        with self._lock:
            events = list(self._media_failures)
        if not events:
            return []
        return list(reversed(events[-limit:]))

    def aggregate_totals(self) -> dict[str, int]:
        with self._lock:
            counters = list(self._counters.values())
        totals: dict[str, int] = {
            "accepted": 0,
            "created": 0,
            "deduped": 0,
            "extracted": 0,
            "errors": 0,
            "media_uploaded": 0,
            "media_deduped": 0,
            "media_failed": 0,
            "http_4xx": 0,
            "http_5xx": 0,
            "auth_failures": 0,
            "forbidden": 0,
            "rate_limited": 0,
            "signature_failures": 0,
        }
        for counter in counters:
            totals["accepted"] += counter.accepted
            totals["created"] += counter.created
            totals["deduped"] += counter.deduped
            totals["extracted"] += counter.extracted
            totals["errors"] += counter.errors
            totals["media_uploaded"] += counter.media_uploaded
            totals["media_deduped"] += counter.media_deduped
            totals["media_failed"] += counter.media_failed
            totals["http_4xx"] += counter.http_4xx
            totals["http_5xx"] += counter.http_5xx
            totals["auth_failures"] += counter.auth_failures
            totals["forbidden"] += counter.forbidden
            totals["rate_limited"] += counter.rate_limited
            totals["signature_failures"] += counter.signature_failures
        return totals


metrics = WhatsAppMetrics()
