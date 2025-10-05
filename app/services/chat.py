"""Service helpers powering the conversational chat tools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from uuid import UUID

from sqlalchemy import and_, func, or_
from sqlmodel import Session, select

from app.db import models


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


class ChatLookupService:
    """Aggregate product and offer lookups for the chat interface tools."""

    def __init__(self, session: Session) -> None:
        self.session = session

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

        base_conditions: list = [
            models.Product.canonical_name.ilike(term),
            models.Product.model_number.ilike(term),
            models.ProductAlias.alias_text.ilike(term),
        ]

        if normalized_query.isdigit():
            base_conditions.append(models.Product.upc == normalized_query)

        tokens = [token for token in normalized_query.split() if token]
        if tokens:
            token_clauses = []
            for token in tokens:
                token_like = f"%{token}%"
                token_clauses.append(
                    or_(
                        models.Product.canonical_name.ilike(token_like),
                        models.Product.model_number.ilike(token_like),
                        models.ProductAlias.alias_text.ilike(token_like),
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

        if not product_ids:
            return ProductMatchPage(matches=[], total=0 if include_total else len(product_ids), has_more=False)

        product_statement = select(models.Product).where(models.Product.id.in_(product_ids))
        products = self.session.exec(product_statement).all()
        product_map = {product.id: product for product in products}

        matches: list[ProductMatch] = []
        for product_id in product_ids:
            product = product_map.get(product_id)
            if not product:
                continue

            source = "unknown"
            if product.canonical_name and normalized_lower in product.canonical_name.lower():
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


__all__ = [
    "ChatLookupService",
    "ProductMatch",
    "ProductMatchPage",
    "OfferBundle",
]
