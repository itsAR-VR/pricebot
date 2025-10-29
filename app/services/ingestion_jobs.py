import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.db import models
from app.db.session import get_session
from app.services.document_ingestion import DocumentIngestResult, ingest_document, _utc_now
from app.services.job_events import job_event_broker

logger = logging.getLogger(__name__)


class IngestionJobRunner:
    """Background executor that processes ingestion jobs sequentially."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ingestion-job")

    def enqueue(self, job_id: UUID) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop_policy().get_event_loop()
        loop.run_in_executor(self._executor, self._run_job_sync, job_id)

    def _run_job_sync(self, job_id: UUID) -> None:
        with get_session() as session:
            job = session.get(models.IngestionJob, job_id)
            if not job:
                logger.error("Ingestion job %s disappeared before execution", job_id)
                return

            source_doc = session.get(models.SourceDocument, job.source_document_id)
            conversation_id = (job.logs or {}).get("conversation_id")
            if not source_doc:
                logger.error("Source document %s missing for job %s", job.source_document_id, job.id)
                job.status = "failed"
                job.logs = {**(job.logs or {}), "error": "Source document is missing"}
                job.updated_at = _utc_now()
                session.add(job)
                session.commit()
                job_event_broker.publish(
                    conversation_id,
                    self._build_event_payload(job, None, message="Source document missing"),
                )
                return

            self._mark_running(session, job, source_doc)
            job_event_broker.publish(
                conversation_id,
                self._build_event_payload(job, source_doc, message="Ingestion started"),
            )

            try:
                result = self._ingest(session, job, source_doc)
            except HTTPException as exc:
                self._mark_failed(
                    session,
                    job,
                    source_doc,
                    conversation_id,
                    str(exc.detail) if hasattr(exc, "detail") else str(exc),
                )
                return
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Unexpected error running ingestion job %s", job.id)
                self._mark_failed(session, job, source_doc, conversation_id, str(exc))
                return

            self._mark_completed(session, job, source_doc, conversation_id, result)

    def _ingest(
        self,
        session,
        job: models.IngestionJob,
        source_doc: models.SourceDocument,
    ) -> DocumentIngestResult:
        logs = job.logs or {}
        vendor_name = logs.get("vendor_name") or (source_doc.extra or {}).get("declared_vendor")
        if not vendor_name:
            vendor_name = "Unknown Vendor"
        prefer_llm = logs.get("prefer_llm")
        file_path = Path(source_doc.storage_path)
        extra_context = {}
        if logs.get("source_whatsapp_message_id"):
            extra_context["source_whatsapp_message_id"] = logs["source_whatsapp_message_id"]
        if logs.get("media_caption"):
            extra_context["media_caption"] = logs["media_caption"]
        if logs.get("media_type"):
            extra_context["media_type"] = logs["media_type"]
        return ingest_document(
            session=session,
            source_doc=source_doc,
            processor_name=job.processor,
            vendor_name=vendor_name,
            file_path=file_path,
            prefer_llm=prefer_llm,
            clear_existing=False,
            extra_context=extra_context or None,
        )

    def _mark_running(
        self,
        session,
        job: models.IngestionJob,
        source_doc: models.SourceDocument,
    ) -> None:
        job.status = "running"
        job.updated_at = _utc_now()
        source_doc.status = "processing"
        source_doc.ingest_started_at = _utc_now()
        session.add(job)
        session.add(source_doc)
        session.commit()
        session.refresh(job)
        session.refresh(source_doc)

    def _mark_failed(
        self,
        session,
        job: models.IngestionJob,
        source_doc: models.SourceDocument,
        conversation_id: Optional[str],
        message: str,
    ) -> None:
        logs = job.logs.copy() if job.logs else {}
        logs["error"] = message
        job.logs = logs
        job.status = "failed"
        job.updated_at = _utc_now()
        session.add(job)
        try:
            session.commit()
        except SQLAlchemyError:  # pragma: no cover - defensive
            session.rollback()
            logger.exception("Failed to persist failure state for ingestion job %s", job.id)
        else:
            session.refresh(job)
        job_event_broker.publish(
            conversation_id,
            self._build_event_payload(job, source_doc, message=message, error=message),
        )

    def _mark_completed(
        self,
        session,
        job: models.IngestionJob,
        source_doc: models.SourceDocument,
        conversation_id: Optional[str],
        result: DocumentIngestResult,
    ) -> None:
        logs = job.logs.copy() if job.logs else {}
        logs.update(
            {
                "message": result.message,
                "offers_count": result.offers_count,
                "warnings": result.warnings,
            }
        )
        job.logs = logs
        job.status = result.status
        job.updated_at = _utc_now()
        session.add(job)
        try:
            session.commit()
        except SQLAlchemyError:  # pragma: no cover - defensive
            session.rollback()
            logger.exception("Failed to persist completion state for ingestion job %s", job.id)
            return
        session.refresh(job)
        session.refresh(source_doc)
        job_event_broker.publish(
            conversation_id,
            self._build_event_payload(
                job,
                source_doc,
                message=result.message,
                offers_count=result.offers_count,
                warnings=result.warnings,
            ),
        )

    def _build_event_payload(
        self,
        job: models.IngestionJob,
        source_doc: Optional[models.SourceDocument],
        *,
        message: Optional[str] = None,
        offers_count: Optional[int] = None,
        warnings: Optional[list[str]] = None,
        error: Optional[str] = None,
    ) -> dict[str, Any]:
        logs = job.logs or {}
        filename = logs.get("filename")
        vendor_name = logs.get("vendor_name")
        payload: dict[str, Any] = {
            "job_id": str(job.id),
            "document_id": str(job.source_document_id),
            "job_status": job.status,
            "document_status": source_doc.status if source_doc else None,
            "processor": job.processor,
            "message": message,
            "offers_count": offers_count,
            "warnings": warnings or logs.get("warnings") or [],
            "error": error or logs.get("error"),
            "filename": filename,
            "vendor_name": vendor_name,
            "updated_at": job.updated_at.isoformat() + "Z" if job.updated_at else None,
        }
        return payload


ingestion_job_runner = IngestionJobRunner()

__all__ = ["ingestion_job_runner", "IngestionJobRunner"]
