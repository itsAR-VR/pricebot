import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.api.deps import get_db
from app.api.routes.offers import OfferOut
from app.core.config import settings
from app.db import models
from app.ingestion import registry
from app.services.offers import OfferIngestionService

BASE_DIR = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = BASE_DIR / "storage" / "templates" / "vendor_price_template.xlsx"

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
def download_vendor_template() -> FileResponse:
    if not TEMPLATE_PATH.exists():
        raise HTTPException(status_code=404, detail="Vendor template not found")
    return FileResponse(
        TEMPLATE_PATH,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="vendor_price_template.xlsx",
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
) -> dict:
    original_name = Path(upload_file.filename or "uploaded_file").name
    file_ext = Path(original_name).suffix.lower()
    processor_name = _determine_processor(file_ext, processor_override)

    logger.debug("Processor selected: %s", processor_name)

    try:
        processor_instance = registry.get(processor_name)
    except KeyError as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=400,
            detail=f"Unknown processor: {processor_name}. Available: {list(registry.processors.keys())}",
        ) from exc

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
            "filename": original_name,
        }

    except Exception as exc:
        session.rollback()
        logger.exception("Upload processing failed for document %s", source_doc.id)
        source_doc.status = "failed"
        source_doc.ingest_completed_at = _utc_now()
        if source_doc.extra:
            source_doc.extra["errors"] = [str(exc)]
        else:
            source_doc.extra = {"errors": [str(exc)]}
        session.add(source_doc)
        try:
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            logger.exception(
                "Failed to persist failure status for document %s", source_doc.id
            )
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(exc)}")

@router.post("/upload", summary="Upload and process price documents")
async def upload_document(
    files: list[UploadFile] | None = File(default=None),
    file: UploadFile | None = File(default=None),
    vendor_name: str = Form(...),
    processor: Optional[str] = Form(None),
    session: Session = Depends(get_db),
) -> dict:
    """
    Upload one or many price sheet documents (Excel, CSV, PDF, images, or text files).

    - **file/files**: Document(s) to upload
    - **vendor_name**: Name of the vendor (e.g., "Abdursajid", "SB Technology")
    - **processor**: Optional processor type override (spreadsheet, document_text, whatsapp_text)
    """

    uploads: list[UploadFile] = []
    if files:
        uploads.extend(files)
    if file:
        uploads.append(file)

    if not uploads:
        raise HTTPException(status_code=400, detail="No files were provided for upload")

    storage_root = _ensure_storage_directory(Path(settings.ingestion_storage_dir))
    results: list[dict] = []
    errors: list[dict] = []

    for upload in uploads:
        logger.info(
            "Upload request received: filename=%s vendor=%s processor=%s",
            upload.filename,
            vendor_name,
            processor or "auto",
        )

        try:
            result = await _process_single_upload(
                upload,
                vendor_name=vendor_name,
                processor_override=processor,
                session=session,
                storage_root=storage_root,
            )
            results.append(result)
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

    if not errors and len(results) == 1:
        return results[0]

    if errors and not results:
        aggregated = "; ".join(f"{err['filename']}: {err['detail']}" for err in errors)
        raise HTTPException(status_code=500, detail=f"Processing failed: {aggregated}")

    status = "success" if not errors else "partial_success"
    message = "Processed 0 document(s)"
    if results:
        message = f"Processed {len(results)} document(s)"

    return {
        "status": status,
        "message": message,
        "processed_count": len(results),
        "failed_count": len(errors),
        "processed": results,
        "errors": errors,
    }



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
