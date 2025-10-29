from __future__ import annotations
import logging
import re
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Annotated, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
import pandas as pd
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.api.deps import get_db
from app.api.routes.offers import OfferOut
from app.core.config import settings
from app.db import models
from app.services.document_ingestion import (
    DocumentIngestResult,
    _utc_now,
    ingest_document as run_document_ingest,
)
from app.services.ingestion_jobs import ingestion_job_runner

BASE_DIR = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = BASE_DIR / "storage" / "templates" / "vendor_price_template.xlsx"

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)


def _build_vendor_template_bytes() -> bytes:
    """Return an in-memory Excel workbook with the canonical vendor template columns."""

    columns = ["Item", "Price", "Qty", "Condition", "Location", "Notes"]
    frame = pd.DataFrame(columns=columns)
    buffer = BytesIO()
    frame.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer.getvalue()



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


def _ensure_storage_directory(storage_dir: Path) -> Path:
    """Ensure the storage directory exists and is writable."""

    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.exception("Unable to create storage directory %s", storage_dir)
        raise HTTPException(
            status_code=500, detail=f"Storage directory '{storage_dir}' is not writable"
        ) from exc

    storage_root = _resolve_storage_root(storage_dir)
    try:
        with tempfile.NamedTemporaryFile(
            dir=storage_root, prefix=".pricebot_write_test", delete=True
        ):
            pass
    except OSError as exc:
        logger.exception("Storage directory %s lacks write permissions", storage_root)
        raise HTTPException(
            status_code=500, detail=f"Storage directory '{storage_root}' is not writable"
        ) from exc

    return storage_root


@router.get("/templates/vendor-price", include_in_schema=False)
def download_vendor_template() -> Response:
    if TEMPLATE_PATH.exists():
        return FileResponse(
            TEMPLATE_PATH,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="vendor_price_template.xlsx",
        )

    logger.warning("Vendor template missing at %s; generating fallback workbook.", TEMPLATE_PATH)
    try:
        content = _build_vendor_template_bytes()
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to generate vendor template workbook: %s", exc)
        raise HTTPException(status_code=500, detail="Unable to generate vendor template") from exc

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="vendor_price_template.xlsx"'},
    )


def _remove_file_if_exists(path: Path) -> None:
    """Best-effort file cleanup compatible with older Python versions."""

    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("Unable to remove temporary file %s during cleanup", path, exc_info=True)


_SPREADSHEET_EXTS = {".xlsx", ".xls", ".csv"}
_DOCUMENT_EXTS = {".pdf", ".jpg", ".jpeg", ".png"}
_TEXT_EXTS = {".txt"}
_SUPPORTED_EXTS = _SPREADSHEET_EXTS | _DOCUMENT_EXTS | _TEXT_EXTS


def _determine_processor(file_ext: str, override: Optional[str]) -> str:
    if override and override != "auto":
        return override

    if file_ext in _SPREADSHEET_EXTS:
        return "spreadsheet"
    if file_ext in _DOCUMENT_EXTS:
        return "document_text"
    if file_ext in _TEXT_EXTS:
        return "whatsapp_text"

    supported = ", ".join(sorted(_SUPPORTED_EXTS))
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported file type: {file_ext}. Supported: {supported}",
    )


async def _process_single_upload(
    upload_file: UploadFile,
    *,
    vendor_name: str,
    processor_override: Optional[str],
    session: Session,
    storage_root: Path,
    prefer_llm: Optional[bool],
    conversation_id: Optional[str],
) -> tuple[models.SourceDocument, models.IngestionJob, dict]:
    original_name = Path(upload_file.filename or "uploaded_file").name
    file_ext = Path(original_name).suffix.lower()
    processor_name = _determine_processor(file_ext, processor_override)

    logger.debug("Processor selected: %s", processor_name)

    now_utc = _utc_now()
    timestamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    safe_name = _sanitize_filename_for_storage(original_name)
    unique_suffix = uuid4().hex[-8:]
    storage_filename = f"{timestamp}_{unique_suffix}_{safe_name}"
    file_path = storage_root / storage_filename

    content = await upload_file.read()
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
    if prefer_llm is not None:
        metadata_extra["prefer_llm"] = bool(prefer_llm)

    source_doc = models.SourceDocument(
        file_name=original_name,
        file_type=file_ext or processor_name,
        storage_path=storage_path_value,
        status="queued",
        ingest_started_at=None,
        ingest_completed_at=None,
        extra=metadata_extra,
    )
    session.add(source_doc)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        logger.exception("Failed to persist source document metadata for %s", storage_path_value)
        _remove_file_if_exists(file_path)
        raise HTTPException(status_code=500, detail="Failed to persist document metadata") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        logger.exception(
            "Database error while saving source document metadata for %s", storage_path_value
        )
        _remove_file_if_exists(file_path)
        raise HTTPException(
            status_code=500,
            detail="Failed to persist document metadata due to a database error",
        ) from exc

    job_logs: dict[str, object] = {
        "vendor_name": vendor_name,
        "filename": original_name,
    }
    if prefer_llm is not None:
        job_logs["prefer_llm"] = bool(prefer_llm)
    if conversation_id:
        job_logs["conversation_id"] = conversation_id

    job = models.IngestionJob(
        source_document_id=source_doc.id,
        processor=processor_name,
        status="queued",
        logs=job_logs,
    )
    session.add(job)
    try:
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        logger.exception("Failed to persist ingestion job for document %s", source_doc.id)
        _remove_file_if_exists(file_path)
        raise HTTPException(status_code=500, detail="Failed to persist ingestion job metadata") from exc

    session.refresh(source_doc)
    session.refresh(job)
    logger.info("Queued ingestion job %s for document %s", job.id, source_doc.id)
    ingestion_job_runner.enqueue(job.id)

    summary = {
        "job_id": str(job.id),
        "document_id": str(source_doc.id),
        "status": job.status,
        "processor": job.processor,
        "filename": original_name,
        "vendor_name": vendor_name,
        "created_at": job.created_at.isoformat() + "Z" if job.created_at else None,
    }

    return source_doc, job, summary

@router.post("/upload", summary="Upload and process price documents")
async def upload_document(
    files: list[UploadFile] = File(default=[]),
    file: UploadFile | None = File(default=None),
    vendor_name: str = Form(...),
    processor: Optional[str] = Form(None),
    prefer_llm: Optional[bool] = Form(None),
    conversation_id: Optional[str] = Header(default=None, alias="X-Conversation-Id"),
    session: Session = Depends(get_db),
) -> dict:
    """
    Upload one or many price sheet documents (Excel, CSV, PDF, images, or text files).

    - **file/files**: Document(s) to upload
    - **vendor_name**: Name of the vendor (e.g., "Abdursajid", "SB Technology")
    - **processor**: Optional processor type override (spreadsheet, document_text, whatsapp_text)
    - **prefer_llm**: Optional flag to request AI-assisted normalization for supported processors
    """

    uploads: list[UploadFile] = []
    if files:
        uploads.extend(files)
    if file:
        uploads.append(file)

    if not uploads:
        raise HTTPException(status_code=400, detail="No files were provided for upload")

    storage_root = _ensure_storage_directory(Path(settings.ingestion_storage_dir))
    accepted: list[dict] = []
    errors: list[dict] = []

    for upload in uploads:
        logger.info(
            "Upload request received: filename=%s vendor=%s processor=%s prefer_llm=%s",
            upload.filename,
            vendor_name,
            processor or "auto",
            bool(prefer_llm),
        )

        try:
            _, _, summary = await _process_single_upload(
                upload,
                vendor_name=vendor_name,
                processor_override=processor,
                session=session,
                storage_root=storage_root,
                prefer_llm=prefer_llm,
                conversation_id=conversation_id,
            )
            accepted.append(summary)
        except HTTPException as exc:
            errors.append(
                {
                    "filename": Path(upload.filename or "uploaded_file").name,
                    "detail": exc.detail,
                }
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unexpected failure while handling %s", upload.filename)
            errors.append(
                {
                    "filename": Path(upload.filename or "uploaded_file").name,
                    "detail": str(exc),
                }
            )

    if errors and not accepted:
        aggregated = "; ".join(f"{err['filename']}: {err['detail']}" for err in errors)
        raise HTTPException(status_code=500, detail=f"Processing failed: {aggregated}")

    status = "accepted" if not errors else "partial"
    message = f"Queued {len(accepted)} document(s) for ingestion"

    payload: dict[str, object] = {
        "status": status,
        "message": message,
        "accepted_count": len(accepted),
        "failed_count": len(errors),
        "accepted": accepted,
        "failed": errors,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    return JSONResponse(status_code=202, content=payload)



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
    jobs: list["IngestionJobStatus"] = []


class IngestionJobSummary(BaseModel):
    job_id: UUID
    document_id: UUID
    status: str
    processor: str
    filename: Optional[str] = None
    vendor_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class IngestionJobStatus(IngestionJobSummary):
    message: Optional[str] = None
    offers_count: Optional[int] = None
    warnings: list[str] = []
    error: Optional[str] = None
    document_status: Optional[str] = None


def _job_status_from_model(
    job: models.IngestionJob,
    document: Optional[models.SourceDocument] = None,
) -> IngestionJobStatus:
    logs = job.logs or {}
    warnings = logs.get("warnings") or []
    if not isinstance(warnings, list):
        warnings = [str(warnings)]
    message = logs.get("message")
    error = logs.get("error")
    filename = logs.get("filename")
    vendor_name = logs.get("vendor_name")
    return IngestionJobStatus(
        job_id=job.id,
        document_id=job.source_document_id,
        status=job.status,
        processor=job.processor,
        filename=filename if isinstance(filename, str) else None,
        vendor_name=vendor_name if isinstance(vendor_name, str) else None,
        created_at=job.created_at,
        updated_at=job.updated_at,
        message=message if isinstance(message, str) else None,
        offers_count=logs.get("offers_count"),
        warnings=warnings,
        error=error if isinstance(error, str) else None,
        document_status=document.status if document else None,
    )


class DocumentIngestRequest(BaseModel):
    vendor_name: Optional[str] = None
    processor: Optional[str] = None
    force: bool = False


DocumentIngestResponse = DocumentIngestResult


class RelatedDocument(BaseModel):
    id: UUID
    file_name: str
    file_type: str
    status: str
    ingest_started_at: Optional[datetime]
    ingest_completed_at: Optional[datetime]
    offer_ids: list[UUID]
    metadata: Optional[dict]


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


OfferIdsQuery = Annotated[
    list[UUID],
    Query(
        ...,
        alias="offer_ids",
        description="Offer IDs to resolve",
    ),
]


@router.get(
    "/related",
    response_model=list[RelatedDocument],
    summary="List documents related to the provided offer IDs",
)
def related_documents(
    offer_ids: OfferIdsQuery,
    session: Session = Depends(get_db),
) -> list[RelatedDocument]:
    if not offer_ids:
        raise HTTPException(status_code=400, detail="offer_ids must be provided")

    offers = session.exec(
        select(models.Offer).where(models.Offer.id.in_(offer_ids))
    ).all()

    doc_to_offers: dict[UUID, list[UUID]] = {}
    for offer in offers:
        if offer.source_document_id is None:
            continue
        doc_to_offers.setdefault(offer.source_document_id, []).append(offer.id)

    if not doc_to_offers:
        return []

    documents = session.exec(
        select(models.SourceDocument).where(
            models.SourceDocument.id.in_(doc_to_offers.keys())
        )
    ).all()

    related: list[RelatedDocument] = []
    for document in documents:
        related.append(
            RelatedDocument(
                id=document.id,
                file_name=document.file_name,
                file_type=document.file_type,
                status=document.status,
                ingest_started_at=document.ingest_started_at,
                ingest_completed_at=document.ingest_completed_at,
                offer_ids=sorted(doc_to_offers.get(document.id, []), key=str),
                metadata=document.extra,
            )
        )

    return related


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

    jobs = session.exec(
        select(models.IngestionJob)
        .where(models.IngestionJob.source_document_id == document.id)
        .order_by(models.IngestionJob.created_at.desc())
    ).all()
    job_statuses = [_job_status_from_model(job, document) for job in jobs]

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
        jobs=job_statuses,
    )


@router.post("/{document_id}/ingest", response_model=DocumentIngestResponse, summary="Trigger ingestion for a stored document")
def ingest_document(
    document_id: UUID,
    payload: DocumentIngestRequest,
    session: Session = Depends(get_db),
) -> DocumentIngestResponse:
    document = session.get(models.SourceDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    payload_vendor = payload.vendor_name or None
    stored_vendor = document.vendor.name if document.vendor else None
    extra_vendor = (document.extra or {}).get("declared_vendor") if document.extra else None
    vendor_name = payload_vendor or stored_vendor or extra_vendor
    if not vendor_name:
        raise HTTPException(
            status_code=400, detail="Vendor name is required to ingest this document"
        )

    processor_name = payload.processor or (
        (document.extra or {}).get("processor") if document.extra else None
    )
    if not processor_name:
        raise HTTPException(
            status_code=400,
            detail="Processor metadata missing; provide a processor value to ingest",
        )

    file_path = Path(document.storage_path)

    extra = document.extra.copy() if document.extra else {}
    extra.setdefault("declared_vendor", vendor_name)
    extra.setdefault("processor", processor_name)
    document.extra = extra

    if not payload.force and document.status in {"processed", "processed_with_warnings"}:
        warnings = []
        if document.extra and document.extra.get("ingestion_errors"):
            warnings = document.extra["ingestion_errors"]
        return DocumentIngestResponse(
            status=document.status,
            message="Document already processed",
            document_id=str(document.id),
            offers_count=len(document.offers or []),
            warnings=warnings,
        )

    should_clear = bool(document.offers or [])
    prefer_llm_flag = (
        document.extra.get("prefer_llm") if document.extra and "prefer_llm" in document.extra else None
    )
    return run_document_ingest(
        session=session,
        source_doc=document,
        processor_name=processor_name,
        vendor_name=vendor_name,
        file_path=file_path,
        prefer_llm=prefer_llm_flag if prefer_llm_flag is not None else None,
        clear_existing=should_clear,
    )


@router.get("/jobs/{job_id}", response_model=IngestionJobStatus, summary="Get ingestion job status")
def get_ingestion_job(job_id: UUID, session: Session = Depends(get_db)) -> IngestionJobStatus:
    job = session.get(models.IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    document = session.get(models.SourceDocument, job.source_document_id) if job.source_document_id else None
    return _job_status_from_model(job, document)
