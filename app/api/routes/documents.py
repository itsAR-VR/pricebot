import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.deps import get_db
from app.api.routes.offers import OfferOut
from app.core.config import settings
from app.db import models
from app.ingestion import registry
from app.services.offers import OfferIngestionService

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return a timezone-naive UTC timestamp."""

    now = datetime.now(timezone.utc)
    return now.replace(tzinfo=None)


_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9_.-]")

def _sanitize_filename_for_storage(name: str) -> str:
    cleaned = (name or "uploaded_file").strip()
    sanitized = _SANITIZE_PATTERN.sub("_", cleaned)
    if not sanitized or set(sanitized) <= {"_", "."}:
        return "uploaded_file"
    return sanitized


def _resolve_storage_root(storage_dir: Path) -> Path:
    try:
        return storage_dir.resolve(strict=False)
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("Failed to resolve storage directory %s: %s", storage_dir, exc)
        return storage_dir.absolute()


@router.post("/upload", summary="Upload and process a price document")
async def upload_document(
    file: UploadFile = File(...),
    vendor_name: str = Form(...),
    processor: Optional[str] = Form(None),
    session: Session = Depends(get_db),
) -> dict:
    """
    Upload a price sheet document (Excel, CSV, PDF, image, or text file).

    - **file**: The document file to upload
    - **vendor_name**: Name of the vendor (e.g., "Abdursajid", "SB Technology")
    - **processor**: Optional processor type override (spreadsheet, document_text, whatsapp_text)
    """
    logger.info(
        "Upload request received: filename=%s vendor=%s processor=%s",
        file.filename,
        vendor_name,
        processor or "auto",
    )

    original_name = Path(file.filename or "uploaded_file").name
    file_ext = Path(original_name).suffix.lower()

    if not processor or processor == "auto":
        # Auto-detect based on file extension
        if file_ext in {".xlsx", ".xls", ".csv"}:
            processor_name = "spreadsheet"
        elif file_ext in {".pdf", ".jpg", ".jpeg", ".png"}:
            processor_name = "document_text"
        elif file_ext == ".txt":
            processor_name = "whatsapp_text"
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {file_ext}. Supported: .xlsx, .xls, .csv, .pdf, .jpg, .png, .txt"
            )
    else:
        processor_name = processor

    logger.debug("Processor selected: %s", processor_name)

    try:
        processor_instance = registry.get(processor_name)
    except KeyError as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=400,
            detail=f"Unknown processor: {processor_name}. Available: {list(registry.processors.keys())}"
        ) from exc

    storage_dir = Path(settings.ingestion_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_root = _resolve_storage_root(storage_dir)

    now_utc = _utc_now()
    timestamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    safe_name = _sanitize_filename_for_storage(original_name)
    storage_filename = f"{timestamp}_{safe_name}"
    file_path = storage_root / storage_filename

    content = await file.read()
    try:
        file_path.write_bytes(content)
    except OSError as exc:
        logger.exception("Failed to persist uploaded file to %s", file_path)
        raise HTTPException(status_code=500, detail="Failed to persist uploaded file to storage") from exc

    storage_path_value = file_path.as_posix()
    logger.info("Saved upload to %s (%d bytes)", storage_path_value, len(content))

    metadata_extra = {
        "original_filename": original_name,
        "original_path": storage_path_value,
        "storage_filename": storage_filename,
        "processor": processor_name,
        "declared_vendor": vendor_name,
        "file_size": len(content),
    }

    source_doc = models.SourceDocument(
        file_name=original_name,
        file_type=file_ext or processor_name,
        storage_path=storage_path_value,
        status="processing",
        ingest_started_at=now_utc,
        extra=metadata_extra,
    )
    session.add(source_doc)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        logger.exception("Failed to persist source document metadata for %s", storage_path_value)
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Failed to persist document metadata") from exc
    session.refresh(source_doc)
    logger.info("Source document persisted: id=%s path=%s", source_doc.id, storage_path_value)

    try:
        logger.info(
            "Starting ingestion for document %s using %s",
            source_doc.id,
            processor_name,
        )
        result = processor_instance.process(file_path, context={"vendor_name": vendor_name})

        offer_service = OfferIngestionService(session)
        persisted = offer_service.ingest(
            offers=result.offers,
            vendor_name=vendor_name,
            source_document=source_doc,
        )
        if persisted:
            source_doc.vendor_id = persisted[0].vendor_id
        logger.info(
            "Ingestion finished: document_id=%s offers=%d warnings=%d",
            source_doc.id,
            len(persisted),
            len(result.errors),
        )

        if result.errors:
            logger.warning("Ingestion warnings for document %s: %s", source_doc.id, result.errors)
            if source_doc.extra is None:
                source_doc.extra = {}
            source_doc.extra["ingestion_errors"] = result.errors

        source_doc.status = "processed" if not result.errors else "processed_with_warnings"
        source_doc.ingest_completed_at = _utc_now()
        session.commit()
        logger.info("Upload completed: document_id=%s status=%s", source_doc.id, source_doc.status)

        return {
            "status": "success",
            "message": f"Processed {len(persisted)} offers",
            "document_id": str(source_doc.id),
            "offers_count": len(persisted),
        }

    except Exception as e:
        session.rollback()
        logger.exception("Upload processing failed for document %s", source_doc.id)
        source_doc.status = "failed"
        source_doc.ingest_completed_at = _utc_now()
        if source_doc.extra:
            source_doc.extra["errors"] = [str(e)]
        else:
            source_doc.extra = {"errors": [str(e)]}
        session.add(source_doc)
        session.commit()

        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")



class DocumentOut(BaseModel):
    id: UUID
    file_name: str
    file_type: str
    status: str
    ingest_started_at: Optional[datetime]
    ingest_completed_at: Optional[datetime]
    offer_count: int
    metadata: Optional[dict]


class DocumentDetail(DocumentOut):
    offers: list[OfferOut]


@router.get("", response_model=list[DocumentOut], summary="List ingested source documents")
def list_documents(
    session: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[DocumentOut]:
    statement = (
        select(models.SourceDocument)
        .order_by(models.SourceDocument.ingest_started_at.desc())
        .offset(offset)
        .limit(limit)
    )
    documents = session.exec(statement).all()

    return [
        DocumentOut(
            id=document.id,
            file_name=document.file_name,
            file_type=document.file_type,
            status=document.status,
            ingest_started_at=document.ingest_started_at,
            ingest_completed_at=document.ingest_completed_at,
            offer_count=len(document.offers or []),
            metadata=document.extra,
        )
        for document in documents
    ]


@router.get("/{document_id}", response_model=DocumentDetail, summary="Get document detail")
def get_document(document_id: UUID, session: Session = Depends(get_db)) -> DocumentDetail:
    document = session.get(models.SourceDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    offers = [
        OfferOut(
            id=offer.id,
            product_id=offer.product_id,
            vendor_id=offer.vendor_id,
            product_name=offer.product.canonical_name if offer.product else "Unknown",
            vendor_name=offer.vendor.name if offer.vendor else "Unknown",
            price=offer.price,
            currency=offer.currency,
            captured_at=offer.captured_at,
            condition=offer.condition,
            quantity=offer.quantity,
            location=offer.location,
        )
        for offer in document.offers or []
    ]

    return DocumentDetail(
        id=document.id,
        file_name=document.file_name,
        file_type=document.file_type,
        status=document.status,
        ingest_started_at=document.ingest_started_at,
        ingest_completed_at=document.ingest_completed_at,
        offer_count=len(offers),
        metadata=document.extra,
        offers=offers,
    )
