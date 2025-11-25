#!/usr/bin/env python3
"""Backfill embeddings for ProductAlias records.

This script generates embeddings for all ProductAlias rows where the embedding
column is NULL, using OpenAI's text-embedding-3-small model.

Usage:
    python scripts/backfill_embeddings.py

Environment Variables:
    ENABLE_OPENAI=true
    OPENAI_API_KEY=sk-...
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import select

from app.core.config import settings
from app.db.models import ProductAlias
from app.db.session import get_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Batch size for processing
BATCH_SIZE = 50

# Embedding model
EMBEDDING_MODEL = "text-embedding-3-small"


def get_openai_client():
    """Initialize and return OpenAI client."""
    try:
        import openai
    except ImportError:
        logger.error("openai package not installed. Run: pip install 'pricebot[llm]'")
        sys.exit(1)

    if not settings.openai_api_key:
        logger.error("OPENAI_API_KEY environment variable not set")
        sys.exit(1)

    return openai.OpenAI(api_key=settings.openai_api_key)


def generate_embedding(client, text: str) -> list[float]:
    """Generate embedding for a single text string."""
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


def backfill_embeddings():
    """Main backfill function."""
    if not settings.enable_openai:
        logger.error("OpenAI is not enabled. Set ENABLE_OPENAI=true")
        sys.exit(1)

    client = get_openai_client()
    logger.info("OpenAI client initialized with model: %s", EMBEDDING_MODEL)

    with get_session() as session:
        # Count total aliases needing embeddings
        count_stmt = select(ProductAlias).where(ProductAlias.embedding.is_(None))
        total_count = len(session.exec(count_stmt).all())
        logger.info("Found %d ProductAlias records without embeddings", total_count)

        if total_count == 0:
            logger.info("All records already have embeddings. Nothing to do.")
            return

        processed = 0
        errors = 0

        while True:
            # Fetch batch of aliases without embeddings (always from start since we update them)
            stmt = (
                select(ProductAlias)
                .where(ProductAlias.embedding.is_(None))
                .limit(BATCH_SIZE)
            )
            aliases = session.exec(stmt).all()

            if not aliases:
                break

            logger.info(
                "Processing batch: %d-%d of %d",
                processed + 1,
                min(processed + len(aliases), total_count),
                total_count,
            )

            for alias in aliases:
                try:
                    # Generate embedding for alias text
                    embedding = generate_embedding(client, alias.alias_text)
                    alias.embedding = embedding
                    session.add(alias)
                    processed += 1

                    if processed % 10 == 0:
                        logger.info(
                            "  Progress: %d/%d (%.1f%%)",
                            processed,
                            total_count,
                            100 * processed / total_count,
                        )

                except Exception as exc:
                    logger.error("Failed to generate embedding for alias %s: %s", alias.id, exc)
                    errors += 1
                    continue

            # Commit batch
            try:
                session.commit()
                logger.info("Batch committed successfully")
            except Exception as exc:
                logger.error("Failed to commit batch: %s", exc)
                session.rollback()
                errors += len(aliases)

        logger.info("=" * 50)
        logger.info("Backfill complete!")
        logger.info("  Processed: %d", processed)
        logger.info("  Errors: %d", errors)
        logger.info("  Success rate: %.1f%%", 100 * processed / max(1, processed + errors))


if __name__ == "__main__":
    logger.info("Starting embedding backfill...")
    backfill_embeddings()

