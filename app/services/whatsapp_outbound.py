"""WhatsApp outbound messaging service.

Provides functionality for sending messages to WhatsApp chats.
Currently implements a mock/logging implementation that records messages
to the database. Replace with actual relay endpoint when available.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlmodel import Session, select

from app.db import models

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return a timezone-naive UTC timestamp for database storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WhatsAppOutboundService:
    """Service for sending outbound WhatsApp messages.

    Currently a mock implementation that:
    1. Validates the chat exists
    2. Records the message in the database with is_outgoing=True
    3. Logs the "sent" message to console

    Future: Replace _send_to_relay with actual HTTP POST to Chrome extension
    or WhatsApp Business API relay endpoint.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def send_text(self, chat_id: UUID, message: str) -> dict:
        """Send a text message to a WhatsApp chat.

        Args:
            chat_id: UUID of the WhatsApp chat to send to
            message: Text content of the message

        Returns:
            dict with keys:
                - success: bool indicating if message was recorded
                - message_id: UUID of the created message (if successful)
                - error: error message (if failed)
        """
        # 1. Validate chat exists
        chat = self.session.get(models.WhatsAppChat, chat_id)
        if not chat:
            logger.warning("Attempted to send message to non-existent chat: %s", chat_id)
            return {
                "success": False,
                "message_id": None,
                "error": f"Chat not found: {chat_id}",
            }

        # 2. Create message record with is_outgoing=True
        msg = models.WhatsAppMessage(
            chat_id=chat.id,
            text=message.strip(),
            is_outgoing=True,
            observed_at=_utcnow(),
            sender_name="Pricebot",  # System sender
            raw_payload={"source": "pricebot_outbound", "status": "sent"},
        )
        self.session.add(msg)

        try:
            self.session.commit()
            self.session.refresh(msg)
        except Exception as exc:
            logger.error("Failed to persist outbound message: %s", exc)
            self.session.rollback()
            return {
                "success": False,
                "message_id": None,
                "error": f"Database error: {exc}",
            }

        # 3. Attempt to send via relay (mock for now)
        relay_success = self._send_to_relay(chat, message)

        # 4. Update message status based on relay result
        if relay_success:
            msg.raw_payload = {**(msg.raw_payload or {}), "status": "sent", "relay": "mock"}
            logger.info(
                "Outbound message sent to chat '%s' (id=%s): %s",
                chat.title,
                msg.id,
                message[:100] + "..." if len(message) > 100 else message,
            )
        else:
            msg.raw_payload = {**(msg.raw_payload or {}), "status": "pending", "relay": "failed"}
            logger.warning("Message recorded but relay failed for chat '%s'", chat.title)

        self.session.add(msg)
        self.session.commit()

        return {
            "success": True,
            "message_id": str(msg.id),
            "chat_title": chat.title,
            "status": msg.raw_payload.get("status", "sent"),
        }

    def _send_to_relay(self, chat: models.WhatsAppChat, message: str) -> bool:
        """Send message to the WhatsApp relay endpoint.

        Currently a MOCK implementation that logs the message.
        Replace this method with actual HTTP POST when relay is configured.

        Args:
            chat: WhatsApp chat object
            message: Message text to send

        Returns:
            True if relay accepted the message, False otherwise
        """
        # TODO: Replace with actual relay implementation
        # Example future implementation:
        #
        # from app.core.config import settings
        # relay_url = settings.whatsapp_relay_url
        # if not relay_url:
        #     return False
        #
        # import httpx
        # response = httpx.post(
        #     f"{relay_url}/send",
        #     json={"chat_id": chat.platform_id, "message": message},
        #     timeout=10,
        # )
        # return response.status_code == 200

        logger.info(
            "[MOCK RELAY] Would send to chat '%s' (platform_id=%s): %s",
            chat.title,
            chat.platform_id,
            message[:50] + "..." if len(message) > 50 else message,
        )
        return True  # Mock always succeeds

    def get_chat_history(
        self,
        chat_id: UUID,
        *,
        limit: int = 50,
        include_outgoing: bool = True,
    ) -> list[models.WhatsAppMessage]:
        """Retrieve recent message history for a chat.

        Args:
            chat_id: UUID of the chat
            limit: Maximum number of messages to return
            include_outgoing: Whether to include outbound messages

        Returns:
            List of WhatsAppMessage objects ordered by observed_at descending
        """
        stmt = select(models.WhatsAppMessage).where(models.WhatsAppMessage.chat_id == chat_id)

        if not include_outgoing:
            stmt = stmt.where(
                (models.WhatsAppMessage.is_outgoing.is_(None))
                | (models.WhatsAppMessage.is_outgoing == False)  # noqa: E712
            )

        stmt = stmt.order_by(models.WhatsAppMessage.observed_at.desc()).limit(limit)

        return list(self.session.exec(stmt).all())


__all__ = ["WhatsAppOutboundService"]

