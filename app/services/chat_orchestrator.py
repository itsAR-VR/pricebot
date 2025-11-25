"""Chat Orchestrator - The brain that connects inbound messages to responses.

This service:
1. Receives notifications of new incoming WhatsApp messages
2. Determines user intent and extracts product mentions
3. Resolves products using RAG (ChatLookupService)
4. Generates contextual responses using LLM
5. Sends responses via WhatsAppOutboundService
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlmodel import Session, select

from app.core.config import settings
from app.core.prompts import (
    NEGOTIATION_SYSTEM_PROMPT,
    NEGOTIATION_USER_CONTEXT_TEMPLATE,
    FALLBACK_RESPONSES,
)
from app.db import models
from app.services.chat import ChatLookupService
from app.services.whatsapp_outbound import WhatsAppOutboundService

if TYPE_CHECKING:
    import openai

logger = logging.getLogger(__name__)

# Configuration
MESSAGE_STALENESS_MINUTES = 5  # Ignore messages older than this
MAX_HISTORY_MESSAGES = 5  # Context window for conversation
RESPONSE_MAX_TOKENS = 300


def _utcnow() -> datetime:
    """Return timezone-naive UTC timestamp."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ChatOrchestrator:
    """Orchestrates the negotiation flow from inbound message to outbound response.
    
    Flow:
    1. handle_incoming_message(message_id) - entry point
    2. _should_respond(message) - check if we should respond
    3. _get_conversation_context(chat_id) - fetch recent messages
    4. _resolve_products(user_text) - find relevant products via RAG
    5. _generate_response(context) - LLM generates reply
    6. _send_response(chat_id, response) - send via WhatsApp
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._llm_client: "openai.OpenAI | None" = None

    def handle_incoming_message(self, message_id: UUID) -> dict:
        """Main entry point - process an incoming WhatsApp message.
        
        Args:
            message_id: UUID of the WhatsAppMessage to process
            
        Returns:
            dict with processing result:
                - processed: bool
                - response_sent: bool  
                - reason: str (if not processed)
                - response_id: str (if response sent)
        """
        # 1. Fetch the message
        message = self.session.get(models.WhatsAppMessage, message_id)
        if not message:
            logger.warning("Message not found: %s", message_id)
            return {"processed": False, "response_sent": False, "reason": "message_not_found"}

        # 2. Check if we should respond
        should_respond, reason = self._should_respond(message)
        if not should_respond:
            logger.debug("Skipping message %s: %s", message_id, reason)
            return {"processed": False, "response_sent": False, "reason": reason}

        logger.info(
            "Processing message from chat '%s': %s",
            message.chat.title if message.chat else "Unknown",
            message.text[:50] if message.text else "[no text]",
        )

        # 3. Get conversation context
        chat = message.chat
        if not chat:
            return {"processed": False, "response_sent": False, "reason": "chat_not_found"}

        history = self._get_conversation_context(chat.id)
        
        # 4. Resolve products from the message
        user_text = message.text or ""
        product_context = self._resolve_products(user_text)

        # 5. Generate response
        response_text = self._generate_response(
            chat_title=chat.title,
            message_history=history,
            product_context=product_context,
            user_message=user_text,
        )

        if not response_text:
            logger.warning("No response generated for message %s", message_id)
            return {"processed": True, "response_sent": False, "reason": "no_response_generated"}

        # 6. Send response
        result = self._send_response(chat.id, response_text)
        
        return {
            "processed": True,
            "response_sent": result.get("success", False),
            "response_id": result.get("message_id"),
            "response_text": response_text[:100],
        }

    def _should_respond(self, message: models.WhatsAppMessage) -> tuple[bool, str]:
        """Determine if we should respond to this message.
        
        Returns:
            (should_respond, reason) tuple
        """
        # Skip outgoing messages (prevent loops)
        if message.is_outgoing:
            return False, "is_outgoing"

        # Skip stale messages (prevent loops on restart)
        if message.observed_at:
            age = _utcnow() - message.observed_at
            if age > timedelta(minutes=MESSAGE_STALENESS_MINUTES):
                return False, f"stale_message_{age.total_seconds():.0f}s"

        # Skip empty messages
        if not message.text or not message.text.strip():
            return False, "empty_message"

        # Skip media-only messages for now (could process captions later)
        text = message.text.strip()
        if text.startswith("[") and text.endswith("]") and len(text) < 20:
            return False, "media_placeholder"

        # Check if OpenAI is enabled for response generation
        if not settings.enable_openai:
            logger.debug("OpenAI disabled, using fallback responses only")
            # We can still respond with fallbacks, so don't skip
            
        return True, "ok"

    def _get_conversation_context(self, chat_id: UUID, limit: int = MAX_HISTORY_MESSAGES) -> str:
        """Fetch recent messages from the chat for context.
        
        Returns formatted string of recent messages.
        """
        stmt = (
            select(models.WhatsAppMessage)
            .where(models.WhatsAppMessage.chat_id == chat_id)
            .order_by(models.WhatsAppMessage.observed_at.desc())
            .limit(limit + 1)  # +1 to exclude current message if needed
        )
        messages = list(self.session.exec(stmt).all())
        
        if not messages:
            return "(No previous messages)"

        # Format messages oldest-first for context
        lines = []
        for msg in reversed(messages[:limit]):
            sender = "Bot" if msg.is_outgoing else (msg.sender_name or "User")
            text = (msg.text or "")[:200]
            lines.append(f"{sender}: {text}")

        return "\n".join(lines) if lines else "(No previous messages)"

    def _resolve_products(self, user_text: str) -> str:
        """Use RAG to find relevant products for the user's query.
        
        Returns formatted product context string.
        """
        if not user_text.strip():
            return "(No product query)"

        lookup = ChatLookupService(self.session)
        
        try:
            result = lookup.resolve_products(user_text, limit=3)
        except Exception as exc:
            logger.error("Product resolution failed: %s", exc)
            return "(Product lookup error)"

        if not result.matches:
            return "(No products found matching query)"

        # Format product info with prices
        lines = []
        for match in result.matches:
            product = match.product
            name = product.canonical_name or "Unknown Product"
            model = product.model_number or ""
            
            # Get best offers for this product
            bundles = lookup.fetch_best_offers([product.id], max_offers=2)
            if bundles and bundles[0].offers:
                offers = bundles[0].offers
                best = offers[0]
                price_str = f"${best.price:.2f}" if best.price else "Price N/A"
                vendor = best.vendor.name if best.vendor else "Unknown Vendor"
                lines.append(f"- {name} ({model}): {price_str} from {vendor}")
                
                # Add price range if multiple offers
                if len(offers) > 1:
                    prices = [o.price for o in offers if o.price]
                    if prices:
                        lines.append(f"  Range: ${min(prices):.2f} - ${max(prices):.2f}")
            else:
                lines.append(f"- {name} ({model}): No current offers")

        return "\n".join(lines) if lines else "(No products found)"

    def _generate_response(
        self,
        *,
        chat_title: str,
        message_history: str,
        product_context: str,
        user_message: str,
    ) -> str | None:
        """Generate a response using LLM or fallback.
        
        Returns response text or None if generation fails.
        """
        # Try LLM first
        if settings.enable_openai and settings.openai_api_key:
            try:
                return self._generate_llm_response(
                    chat_title=chat_title,
                    message_history=message_history,
                    product_context=product_context,
                    user_message=user_message,
                )
            except Exception as exc:
                logger.error("LLM response generation failed: %s", exc)
                # Fall through to fallback

        # Fallback responses based on simple pattern matching
        return self._generate_fallback_response(user_message, product_context)

    def _generate_llm_response(
        self,
        *,
        chat_title: str,
        message_history: str,
        product_context: str,
        user_message: str,
    ) -> str | None:
        """Generate response using OpenAI chat completion."""
        client = self._ensure_llm_client()
        
        user_prompt = NEGOTIATION_USER_CONTEXT_TEMPLATE.format(
            chat_title=chat_title,
            message_history=message_history,
            product_context=product_context,
            user_message=user_message,
        )

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.7,
                max_tokens=RESPONSE_MAX_TOKENS,
                messages=[
                    {"role": "system", "content": NEGOTIATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content
            return content.strip() if content else None
        except Exception as exc:
            logger.error("OpenAI API error: %s", exc)
            return None

    def _generate_fallback_response(self, user_message: str, product_context: str) -> str:
        """Generate a simple fallback response without LLM."""
        msg_lower = user_message.lower()
        
        # Greeting detection
        greetings = {"hi", "hello", "hey", "good morning", "good afternoon", "thanks", "thank you"}
        if any(g in msg_lower for g in greetings):
            return FALLBACK_RESPONSES["greeting"]

        # Product found?
        if "No products found" in product_context or "No product query" in product_context:
            return FALLBACK_RESPONSES["no_product_found"]

        # Default: echo product info
        if product_context and "- " in product_context:
            return f"Here's what I found:\n{product_context}"

        return FALLBACK_RESPONSES["confirmation"]

    def _send_response(self, chat_id: UUID, response_text: str) -> dict:
        """Send response via WhatsApp outbound service."""
        outbound = WhatsAppOutboundService(self.session)
        result = outbound.send_text(chat_id, response_text)
        
        if result.get("success"):
            logger.info(
                "Response sent to chat %s: %s",
                chat_id,
                response_text[:50],
            )
        else:
            logger.error("Failed to send response: %s", result.get("error"))

        return result

    def _ensure_llm_client(self) -> "openai.OpenAI":
        """Lazily initialize OpenAI client."""
        if self._llm_client is not None:
            return self._llm_client

        try:
            import openai
        except ImportError as exc:
            raise RuntimeError("openai package not available") from exc

        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")

        self._llm_client = openai.OpenAI(api_key=settings.openai_api_key)
        return self._llm_client


# -----------------------------------------------------------------------------
# Background Task Integration
# -----------------------------------------------------------------------------

def trigger_orchestrator_background(message_id: UUID) -> None:
    """Background task to process a message through the orchestrator.
    
    This function is designed to be called from FastAPI BackgroundTasks
    or similar async task runners.
    """
    from app.db.session import get_session

    logger.info("Background orchestrator triggered for message %s", message_id)
    
    try:
        with get_session() as session:
            orchestrator = ChatOrchestrator(session)
            result = orchestrator.handle_incoming_message(message_id)
            logger.info("Orchestrator result for %s: %s", message_id, result)
    except Exception as exc:
        logger.exception("Orchestrator background task failed for %s: %s", message_id, exc)


__all__ = [
    "ChatOrchestrator",
    "trigger_orchestrator_background",
]

