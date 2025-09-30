from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_db
from app.api.routes.offers import OfferOut
from app.core.config import settings
from app.db import models
from app.ingestion import registry
from app.services.offers import OfferIngestionService

router = APIRouter(prefix="/documents", tags=["documents"])


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
    # Determine processor
    if not processor or processor == "auto":
        # Auto-detect based on file extension
        file_ext = Path(file.filename or "").suffix.lower()
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
    
    # Get processor
    proc_cls = registry.get(processor_name)
    if not proc_cls:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown processor: {processor_name}. Available: {list(registry.processors.keys())}"
        )
    
    # Save file to storage
    storage_dir = Path(settings.ingestion_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    safe_filename = f"{timestamp}_{file.filename}"
    file_path = storage_dir / safe_filename
    
    # Write uploaded file
    content = await file.read()
    file_path.write_bytes(content)
    
    # Create source document record
    source_doc = models.SourceDocument(
        file_name=file.filename or "uploaded_file",
        file_type=Path(file.filename or "").suffix.lower(),
        status="processing",
        ingest_started_at=datetime.utcnow(),
        extra={"original_path": str(file_path), "processor": processor_name, "declared_vendor": vendor_name}
    )
    session.add(source_doc)
    session.commit()
    session.refresh(source_doc)
    
    # Process the file
    try:
        processor_instance = proc_cls()
        raw_offers = processor_instance.process(str(file_path))
        
        # Ingest offers
        offer_service = OfferIngestionService(session)
        offer_service.ingest(
            offers=raw_offers,
            vendor_name=vendor_name,
            source_document=source_doc
        )
        
        # Update document status
        source_doc.status = "processed"
        source_doc.ingest_completed_at = datetime.utcnow()
        session.commit()
        
        return {
            "status": "success",
            "message": f"Processed {len(raw_offers)} offers",
            "document_id": str(source_doc.id),
            "offers_count": len(raw_offers)
        }
    
    except Exception as e:
        # Mark as failed
        source_doc.status = "failed"
        source_doc.ingest_completed_at = datetime.utcnow()
        if source_doc.extra:
            source_doc.extra["errors"] = [str(e)]
        else:
            source_doc.extra = {"errors": [str(e)]}
        session.commit()
        
        raise HTTPException(
            status_code=500,
            detail=f"Processing failed: {str(e)}"
        )


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
