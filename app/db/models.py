from datetime import datetime
from typing import Optional, List
from uuid import UUID, uuid4

from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


class TimestampMixin(SQLModel):
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class Vendor(SQLModel, table=True):
    __tablename__ = "vendors"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    name: str = Field(index=True, nullable=False)
    contact_info: dict | None = Field(default=None, sa_column=Column(JSON))
    extra: dict | None = Field(default=None, sa_column=Column(JSON))

    products: List["Product"] = Relationship(back_populates="default_vendor", sa_relationship_kwargs={"lazy": "selectin"})
    documents: List["SourceDocument"] = Relationship(back_populates="vendor", sa_relationship_kwargs={"lazy": "selectin"})


class Product(SQLModel, table=True):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("brand", "model_number", name="uq_products_brand_model"),
        UniqueConstraint("upc", name="uq_products_upc"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    canonical_name: str = Field(index=True)
    brand: Optional[str] = Field(default=None, index=True)
    model_number: Optional[str] = Field(default=None, index=True)
    upc: Optional[str] = Field(default=None, index=True)
    category: Optional[str] = Field(default=None, index=True)
    spec: dict | None = Field(default=None, sa_column=Column(JSON))

    default_vendor_id: Optional[UUID] = Field(default=None, foreign_key="vendors.id")
    default_vendor: Optional[Vendor] = Relationship(back_populates="products")

    aliases: List["ProductAlias"] = Relationship(back_populates="product", sa_relationship_kwargs={"lazy": "selectin"})
    offers: List["Offer"] = Relationship(back_populates="product", sa_relationship_kwargs={"lazy": "selectin"})


class ProductAlias(SQLModel, table=True):
    __tablename__ = "product_aliases"
    __table_args__ = (UniqueConstraint("product_id", "alias_text", name="uq_alias_product_text"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    product_id: UUID = Field(foreign_key="products.id", nullable=False)
    alias_text: str = Field(index=True)
    source_vendor_id: Optional[UUID] = Field(default=None, foreign_key="vendors.id")
    embedding: list[float] | None = Field(default=None, sa_column=Column(JSON))

    product: Product = Relationship(back_populates="aliases", sa_relationship_kwargs={"lazy": "selectin"})
    source_vendor: Optional[Vendor] = Relationship(sa_relationship_kwargs={"lazy": "selectin"})


class SourceDocument(SQLModel, table=True):
    __tablename__ = "source_documents"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    vendor_id: UUID | None = Field(default=None, foreign_key="vendors.id")
    file_name: str = Field(nullable=False)
    file_type: str = Field(nullable=False, index=True)
    storage_path: str = Field(nullable=False)
    ingest_started_at: datetime | None = Field(default=None)
    ingest_completed_at: datetime | None = Field(default=None)
    status: str = Field(default="pending", index=True)
    extra: dict | None = Field(default=None, sa_column=Column(JSON))

    vendor: Optional[Vendor] = Relationship(back_populates="documents", sa_relationship_kwargs={"lazy": "selectin"})
    offers: List["Offer"] = Relationship(back_populates="source_document", sa_relationship_kwargs={"lazy": "selectin"})
    ingestion_jobs: List["IngestionJob"] = Relationship(back_populates="source_document", sa_relationship_kwargs={"lazy": "selectin"})


class Offer(SQLModel, table=True):
    __tablename__ = "offers"
    __table_args__ = (
        UniqueConstraint("product_id", "vendor_id", "captured_at", "price", name="uq_offer_unique_snapshot"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    product_id: UUID = Field(foreign_key="products.id", nullable=False)
    vendor_id: UUID = Field(foreign_key="vendors.id", nullable=False)
    source_document_id: UUID | None = Field(default=None, foreign_key="source_documents.id")
    captured_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    price: float = Field(nullable=False)
    currency: str = Field(default="USD", nullable=False)
    quantity: int | None = Field(default=None)
    condition: str | None = None
    min_order_quantity: int | None = None
    location: str | None = None
    notes: str | None = None
    raw_payload: dict | None = Field(default=None, sa_column=Column(JSON))

    product: Product = Relationship(back_populates="offers", sa_relationship_kwargs={"lazy": "selectin"})
    vendor: Vendor = Relationship(sa_relationship_kwargs={"lazy": "selectin"})
    source_document: Optional[SourceDocument] = Relationship(back_populates="offers", sa_relationship_kwargs={"lazy": "selectin"})
    price_history_entries: List["PriceHistory"] = Relationship(back_populates="source_offer", sa_relationship_kwargs={"lazy": "selectin"})


class PriceHistory(SQLModel, table=True):
    __tablename__ = "price_history"
    __table_args__ = (
        UniqueConstraint("product_id", "vendor_id", "valid_from", name="uq_price_history_span"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    product_id: UUID = Field(foreign_key="products.id", nullable=False)
    vendor_id: UUID = Field(foreign_key="vendors.id", nullable=False)
    price: float = Field(nullable=False)
    currency: str = Field(default="USD")
    valid_from: datetime = Field(default_factory=datetime.utcnow, index=True)
    valid_to: datetime | None = Field(default=None, index=True)
    source_offer_id: UUID = Field(foreign_key="offers.id", nullable=False)

    product: Product = Relationship(sa_relationship_kwargs={"lazy": "selectin"})
    vendor: Vendor = Relationship(sa_relationship_kwargs={"lazy": "selectin"})
    source_offer: Offer = Relationship(back_populates="price_history_entries", sa_relationship_kwargs={"lazy": "selectin"})


class IngestionJob(SQLModel, table=True):
    __tablename__ = "ingestion_jobs"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    source_document_id: UUID = Field(foreign_key="source_documents.id", nullable=False)
    processor: str = Field(nullable=False, index=True)
    status: str = Field(default="queued", index=True)
    logs: dict | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    source_document: SourceDocument = Relationship(back_populates="ingestion_jobs", sa_relationship_kwargs={"lazy": "selectin"})


__all__ = [
    "Vendor",
    "Product",
    "ProductAlias",
    "SourceDocument",
    "Offer",
    "PriceHistory",
    "IngestionJob",
]
