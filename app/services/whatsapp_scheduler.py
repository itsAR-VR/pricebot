from __future__ import annotations

import logging
from threading import Lock, Thread, Timer
from typing import Optional
from uuid import UUID

from app.core.config import settings
from app.core.metrics import metrics
from app.db import models
from app.db.session import get_session
from app.services.whatsapp_extract import WhatsAppExtractionService


logger = logging.getLogger("pricebot.whatsapp.scheduler")


class WhatsAppExtractionScheduler:
    """Debounce WhatsApp extraction triggers to avoid duplicate processing."""

    def __init__(self, debounce_seconds: float) -> None:
        self.debounce_seconds = max(0.0, debounce_seconds)
        self._timers: dict[str, Timer] = {}
        self._lock = Lock()

    def schedule(self, chat_id: UUID, *, client_id: Optional[str]) -> None:
        key = str(chat_id)
        with self._lock:
            existing = self._timers.pop(key, None)
            if existing:
                existing.cancel()

            if self.debounce_seconds <= 0:
                thread = Thread(target=self._run_extraction_safe, args=(key, client_id), daemon=True)
                thread.start()
                return

            timer = Timer(self.debounce_seconds, self._run_extraction_safe, args=(key, client_id))
            timer.daemon = True
            self._timers[key] = timer
            timer.start()

    def _run_extraction_safe(self, chat_key: str, client_id: Optional[str]) -> None:
        try:
            chat_id = UUID(chat_key)
        except ValueError:
            logger.warning("Skipping auto extraction for invalid chat id %s", chat_key)
            return
        try:
            self._perform_extraction(chat_id, client_id)
        finally:
            with self._lock:
                timer = self._timers.pop(chat_key, None)
                if timer:
                    timer.cancel()

    def _perform_extraction(self, chat_id: UUID, client_id: Optional[str]) -> None:
        try:
            with get_session() as session:
                chat = session.get(models.WhatsAppChat, chat_id)
                if not chat:
                    return
                service = WhatsAppExtractionService(session)
                result = service.extract_from_chat(chat, since=chat.last_extracted_at)
                offers = int(result.get("offers", 0) or 0)
                warnings = int(result.get("warnings", 0) or 0)
                metrics.record_extract(
                    client_id=client_id,
                    chat_id=str(chat_id),
                    chat_title=chat.title,
                    offers=offers,
                    errors=warnings,
                )
                session.commit()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Error running WhatsApp auto extraction for chat %s: %s", chat_id, exc)
            metrics.record_extract(
                client_id=client_id,
                chat_id=str(chat_id),
                chat_title=None,
                offers=0,
                errors=1,
            )


scheduler = WhatsAppExtractionScheduler(settings.whatsapp_extract_debounce_seconds)
