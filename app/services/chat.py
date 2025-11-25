"""Service helpers powering the conversational chat tools."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from uuid import UUID

from sqlalchemy import and_, func, or_
from sqlmodel import Session, select

from app.db import models
from app.core.config import settings

logger = logging.getLogger(__name__)

# Embedding model and vector search constants
EMBEDDING_MODEL = "text-embedding-3-small"
VECTOR_SEARCH_THRESHOLD = 3  # Trigger vector search if SQL returns fewer results
VECTOR_SIMILARITY_MIN = 0.3  # Minimum similarity score to include in results


@dataclass
class ProductMatch:
    product: models.Product
    match_source: str


@dataclass
class OfferBundle:
    product: models.Product
    offers: list[models.Offer]


@dataclass
class ProductMatchPage:
    matches: list[ProductMatch]
    total: int
    has_more: bool


@dataclass
class RecentProductSuggestion:
    product_id: UUID
    canonical_name: str
    alias: str | None
    last_seen: datetime | None
    offer_count: int


class ChatLookupService:
    """Aggregate product and offer lookups for the chat interface tools."""

    def __init__(self, session: Session) -> None:
        self.session = session
        # Lazily initialized LLM client
        self._llm_client = None

    def _norm_token(self, text: str) -> str:
        """Normalize a string for robust LIKE matching across punctuation.

        Lowercase and replace common separators with spaces; collapse multiple spaces.
        """
        t = text.lower()
        for ch in (
            "-",
            "_",
            "/",
            ".",
            ",",
            "(",
            ")",
            "[",
            "]",
            "{",
            "}",
            ":",
            ";",
            "'",
            '"',
            "+",
            "#",
            "|",
            "\\",
            "?",
            "!",
            "@",
            "$",
            "%",
            "^",
            "&",
            "*",
        ):
            t = t.replace(ch, " ")
        # collapse consecutive spaces
        t = " ".join(part for part in t.split() if part)
        return t

    def _norm_col(self, col):
        """Return a SQL expression that normalizes a text column similarly to _norm_token."""
        expr = func.lower(col)
        for ch in (
            "-",
            "_",
            "/",
            ".",
            ",",
            "(",
            ")",
            "[",
            "]",
            "{",
            "}",
            ":",
            ";",
            "'",
            '"',
            "+",
            "#",
            "|",
            "?",
            "!",
            "@",
            "$",
            "%",
            "^",
            "&",
            "*",
        ):
            expr = func.replace(expr, ch, " ")
        return expr

    # ------------------------------------------------------------------
    # Vector/Semantic Search Methods
    # ------------------------------------------------------------------
    def _get_query_embedding(self, query: str) -> list[float] | None:
        """Generate an embedding vector for the search query.

        Returns None if OpenAI is not configured or on any error.
        """
        if not settings.enable_openai or not settings.openai_api_key:
            return None

        try:
            client = self._ensure_llm_client()
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=query,
            )
            return response.data[0].embedding
        except Exception as exc:
            logger.warning("Failed to generate query embedding: %s", exc)
            return None

    def _cosine_similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        """Calculate cosine similarity between two vectors using numpy."""
        try:
            import numpy as np
        except ImportError:
            logger.warning("numpy not installed, cannot calculate cosine similarity")
            return 0.0

        a = np.array(vec_a)
        b = np.array(vec_b)
        dot_product = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return float(dot_product / (norm_a * norm_b))

    def _vector_search(
        self,
        query_embedding: list[float],
        *,
        limit: int = 5,
        exclude_product_ids: set[UUID] | None = None,
    ) -> list[tuple[UUID, float]]:
        """Find products by vector similarity search.

        Returns list of (product_id, similarity_score) tuples sorted by score descending.
        """
        exclude_ids = exclude_product_ids or set()

        # Fetch all aliases that have embeddings
        stmt = select(models.ProductAlias).where(models.ProductAlias.embedding.isnot(None))
        aliases = self.session.exec(stmt).all()

        if not aliases:
            logger.debug("No aliases with embeddings found for vector search")
            return []

        # Calculate similarity scores
        scores: list[tuple[UUID, float]] = []
        seen_products: set[UUID] = set()

        for alias in aliases:
            if alias.product_id in exclude_ids or alias.product_id in seen_products:
                continue

            if alias.embedding:
                similarity = self._cosine_similarity(query_embedding, alias.embedding)
                scores.append((alias.product_id, similarity))
                seen_products.add(alias.product_id)

        # Sort by similarity score descending and take top results
        scores.sort(key=lambda x: x[1], reverse=True)
        top_results = scores[:limit]

        if top_results:
            logger.info(
                "Vector search triggered - found %d candidates (top score: %.3f)",
                len(top_results),
                top_results[0][1] if top_results else 0,
            )

        return top_results

    def resolve_products(
        self,
        query: str,
        *,
        limit: int = 5,
        offset: int = 0,
        include_total: bool = False,
    ) -> ProductMatchPage:
        """Return a paginated list of products whose metadata or aliases match the query."""
        if not query:
            return ProductMatchPage(matches=[], total=0, has_more=False)

        normalized_query = query.strip()
        normalized_lower = normalized_query.lower()
        term = f"%{normalized_query}%"
        norm_token_all = self._norm_token(normalized_query)
        norm_term_all = f"%{norm_token_all}%"
        norm_name_col = self._norm_col(models.Product.canonical_name)
        norm_model_col = self._norm_col(models.Product.model_number)

        base_conditions: list = [
            models.Product.canonical_name.ilike(term),
            models.Product.model_number.ilike(term),
            models.ProductAlias.alias_text.ilike(term),
            norm_name_col.ilike(norm_term_all),
            norm_model_col.ilike(norm_term_all),
            self._norm_col(models.ProductAlias.alias_text).ilike(norm_term_all),
        ]

        if normalized_query.isdigit():
            base_conditions.append(models.Product.upc == normalized_query)

        stopwords = {
            "what",
            "whats",
            "what's",
            "the",
            "is",
            "are",
            "a",
            "an",
            "for",
            "of",
            "in",
            "price",
            "prices",
            "cost",
            "much",
            "how",
            "do",
            "you",
            "have",
            "need",
            "i",
            "me",
            "please",
            "show",
            "find",
            "get",
            "want",
            "looking",
        }

        norm_tokens = []
        for token in self._norm_token(normalized_query).split():
            if not token or len(token) <= 1:
                continue
            if token in stopwords:
                continue
            norm_tokens.append(token)

        if norm_tokens:
            token_clauses = []
            for token in norm_tokens:
                token_like = f"%{token}%"
                token_like_norm = f"%{token}%"
                token_clauses.append(
                    or_(
                        models.Product.canonical_name.ilike(token_like),
                        models.Product.model_number.ilike(token_like),
                        models.ProductAlias.alias_text.ilike(token_like),
                        norm_name_col.ilike(token_like_norm),
                        norm_model_col.ilike(token_like_norm),
                        self._norm_col(models.ProductAlias.alias_text).ilike(token_like_norm),
                    )
                )
            base_conditions.append(and_(*token_clauses))

        # Fetch a page of product ids for pagination while avoiding duplicates from alias joins.
        id_statement = (
            select(models.Product.id)
            .select_from(models.Product)
            .outerjoin(models.ProductAlias)
            .where(or_(*base_conditions))
            .group_by(models.Product.id)
            .order_by(func.lower(models.Product.canonical_name))
            .offset(offset)
            .limit(limit + 1)
        )
        id_rows = self.session.exec(id_statement).all()
        has_more = len(id_rows) > limit
        product_ids = [row[0] if isinstance(row, tuple) else row for row in id_rows[:limit]]

        # ------------------------------------------------------------------
        # Vector Search Fallback: If SQL returns < 3 results, try semantic search
        # ------------------------------------------------------------------
        vector_product_ids: list[UUID] = []
        if len(product_ids) < VECTOR_SEARCH_THRESHOLD and settings.enable_openai:
            query_embedding = self._get_query_embedding(normalized_query)
            if query_embedding:
                # Exclude products already found by SQL
                exclude_ids = set(product_ids)
                vector_results = self._vector_search(
                    query_embedding,
                    limit=5,
                    exclude_product_ids=exclude_ids,
                )
                # Filter by minimum similarity threshold
                vector_product_ids = [
                    pid for pid, score in vector_results if score >= VECTOR_SIMILARITY_MIN
                ]
                # Merge vector results with SQL results
                product_ids = list(product_ids) + vector_product_ids
                logger.debug(
                    "Merged %d SQL results with %d vector results",
                    len(product_ids) - len(vector_product_ids),
                    len(vector_product_ids),
                )

        if not product_ids:
            return ProductMatchPage(matches=[], total=0 if include_total else len(product_ids), has_more=False)

        product_statement = select(models.Product).where(models.Product.id.in_(product_ids))
        products = self.session.exec(product_statement).all()
        product_map = {product.id: product for product in products}
        vector_product_id_set = set(vector_product_ids)  # Track which came from vector search

        matches: list[ProductMatch] = []
        for product_id in product_ids:
            product = product_map.get(product_id)
            if not product:
                continue

            # Determine match source
            source = "unknown"
            if product_id in vector_product_id_set:
                # This product came from vector/semantic search
                source = "vector_search"
            elif product.canonical_name and normalized_lower in product.canonical_name.lower():
                source = "canonical_name"
            elif product.model_number and normalized_lower in (product.model_number or "").lower():
                source = "model_number"
            elif product.upc and product.upc == normalized_query:
                source = "upc"
            else:
                for alias in product.aliases or []:
                    alias_text = alias.alias_text or ""
                    if normalized_lower in alias_text.lower():
                        source = "alias"
                        break

            matches.append(ProductMatch(product=product, match_source=source))

        # Optional: LLM-assisted re-ranking/filtering of matches
        matches = self._maybe_llm_rerank(query, matches)

        total = len(matches)
        if include_total:
            count_statement = (
                select(func.count(func.distinct(models.Product.id)))
                .select_from(models.Product)
                .outerjoin(models.ProductAlias)
                .where(or_(*base_conditions))
            )
            total = int(self.session.exec(count_statement).one())

        return ProductMatchPage(matches=matches, total=total, has_more=has_more)

    # ------------------------------------------------------------------
    # LLM-assisted re-ranking
    # ------------------------------------------------------------------
    def _maybe_llm_rerank(self, query: str, matches: list[ProductMatch]) -> list[ProductMatch]:
        """If enabled, ask an LLM to rank and prune candidate matches.

        - If the top candidate confidence >= 0.85, return only that match.
        - If >= 0.60, return top 3.
        - Otherwise, return up to 5 ranked suggestions.

        Falls back to the original list on any LLM error or when disabled.
        """

        if not matches or not settings.enable_openai or not settings.use_llm_product_resolve:
            return matches

        try:
            client = self._ensure_llm_client()
        except Exception:
            return matches

        # Prepare a compact candidate list for the prompt
        candidates = []
        for m in matches[:10]:  # cap to keep prompt small
            p = m.product
            candidates.append({
                "id": str(p.id),
                "name": p.canonical_name,
                "model": (p.model_number or "")[:60],
                "upc": p.upc or "",
                "aliases": [a.alias_text for a in (p.aliases or [])][:5],
            })

        system = (
            "You are a product resolver for a price intelligence system. "
            "Given a user query and a list of known catalog entries, select the best matching products. "
            "Return strictly-formatted JSON with keys: 'ranking' (array of {id, confidence [0-1]}). "
            "Only include IDs from the provided candidates. Confidence reflects semantic match certainty."
        )
        user = {
            "query": query,
            "candidates": candidates,
        }

        try:
            resp = client.chat.completions.create(  # type: ignore[attr-defined]
                model="gpt-4o-mini",
                temperature=0,
                max_tokens=400,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": [{"type": "text", "text": str(user)}]},
                ],
            )
            content = resp.choices[0].message.content or "{}"
            data = self._safe_json(content)
            ranking = data.get("ranking") or []
        except Exception:
            return matches

        # Build maps for quick lookup
        match_map = {str(m.product.id): m for m in matches}
        ordered: list[ProductMatch] = []
        confidences: list[float] = []
        for item in ranking:
            if not isinstance(item, dict):
                continue
            pid = item.get("id")
            conf = item.get("confidence")
            if pid in match_map and isinstance(conf, (int, float)):
                ordered.append(match_map[pid])
                confidences.append(float(conf))

        if not ordered:
            return matches

        top = confidences[0]
        if top >= 0.85:
            return ordered[:1]
        if top >= 0.60:
            return ordered[:3]
        return ordered[:5]

    def _ensure_llm_client(self):
        if self._llm_client is not None:
            return self._llm_client
        try:
            import openai  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("openai package not available") from exc
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")
        self._llm_client = openai.OpenAI(api_key=settings.openai_api_key)
        return self._llm_client

    @staticmethod
    def _safe_json(text: str):
        import json
        text = text.strip()
        if text.startswith("```") and text.endswith("```"):
            body = text.split("\n", 1)[-1]
            if body.endswith("\n```"):
                body = body[: -len("\n```")]
            text = body
        try:
            return json.loads(text)
        except Exception:
            return {}

    def fetch_best_offers(
        self,
        product_ids: Iterable[UUID],
        *,
        vendor_id: UUID | None = None,
        condition: str | None = None,
        location: str | None = None,
        max_offers: int = 5,
        min_price: float | None = None,
        max_price: float | None = None,
        captured_since: datetime | None = None,
    ) -> list[OfferBundle]:
        """Fetch the cheapest offers per product according to the provided filters."""
        bundles: list[OfferBundle] = []
        condition_norm = condition.strip().lower() if condition else None
        location_norm = location.strip() if location else None
        location_term = f"%{location_norm}%" if location_norm else None

        for product_id in product_ids:
            statement = select(models.Offer).where(models.Offer.product_id == product_id)

            if vendor_id:
                statement = statement.where(models.Offer.vendor_id == vendor_id)
            if condition_norm:
                statement = statement.where(func.lower(models.Offer.condition) == condition_norm)
            if location_term:
                statement = statement.where(models.Offer.location.ilike(location_term))
            if min_price is not None:
                statement = statement.where(models.Offer.price >= min_price)
            if max_price is not None:
                statement = statement.where(models.Offer.price <= max_price)
            if captured_since is not None:
                statement = statement.where(models.Offer.captured_at >= captured_since)

            statement = statement.order_by(models.Offer.price.asc(), models.Offer.captured_at.desc()).limit(max_offers)
            offers = list(self.session.exec(statement).all())

            if not offers:
                continue

            product = offers[0].product
            bundles.append(OfferBundle(product=product, offers=offers))

        return bundles

    def fetch_recent_product_summaries(self, *, limit: int = 5) -> list[RecentProductSuggestion]:
        """Return recently ingested products ordered by most recent offer capture."""
        if limit <= 0:
            return []

        statement = (
            select(
                models.Product.id,
                models.Product.canonical_name,
                func.min(models.ProductAlias.alias_text).label("alias_text"),
                func.max(models.Offer.captured_at).label("last_seen"),
                func.count(models.Offer.id).label("offer_count"),
            )
            .join(models.Offer, models.Offer.product_id == models.Product.id)
            .outerjoin(models.ProductAlias, models.ProductAlias.product_id == models.Product.id)
            .group_by(models.Product.id, models.Product.canonical_name)
            .order_by(func.max(models.Offer.captured_at).desc())
            .limit(limit)
        )

        rows = self.session.exec(statement).all()
        suggestions: list[RecentProductSuggestion] = []
        for row in rows:
            product_id, canonical_name, alias_text, last_seen, offer_count = row
            suggestions.append(
                RecentProductSuggestion(
                    product_id=product_id,
                    canonical_name=canonical_name,
                    alias=alias_text,
                    last_seen=last_seen,
                    offer_count=int(offer_count or 0),
                )
            )
        return suggestions


__all__ = [
    "ChatLookupService",
    "ProductMatch",
    "ProductMatchPage",
    "OfferBundle",
    "RecentProductSuggestion",
]
