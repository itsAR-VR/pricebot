from __future__ import annotations

import argparse
from datetime import datetime, timezone
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.db.session import get_session, init_db
from app.ingestion import registry
from app.db import models
from app.services.offers import OfferIngestionService


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest a pricing source file into the database")
    parser.add_argument("file", type=Path, help="Path to the source file")
    parser.add_argument("--vendor", required=False, help="Override vendor name")
    parser.add_argument("--currency", required=False, help="Override currency code")
    parser.add_argument(
        "--processor",
        required=False,
        choices=sorted(registry.processors.keys()),
        help="Force a specific ingestion processor",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    file_path: Path = args.file
    if not file_path.exists():
        print(f"File not found: {file_path}")
        return 1

    if args.processor:
        processor = registry.get(args.processor)
    else:
        processor = registry.match(file_path)
    if not processor:
        print(f"No processor registered for file type: {file_path.suffix}")
        return 1

    context: dict[str, Any] = {
        "vendor_name": args.vendor,
        "currency": args.currency or settings.default_currency,
    }

    storage_dir = settings.ingestion_storage_dir
    storage_dir.mkdir(parents=True, exist_ok=True)
    copied_path = storage_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex}{file_path.suffix}"
    shutil.copy2(file_path, copied_path)

    ingest_started_at = datetime.now(timezone.utc)
    result = processor.process(file_path, context=context)
    if result.errors:
        print("Encountered errors during parsing:")
        for error in result.errors:
            print(f" - {error}")

    init_db()
    with get_session() as session:
        source_metadata = {
        "original_path": str(file_path.resolve()),
        "processor": processor.name,
    }
        if args.vendor:
            source_metadata["declared_vendor"] = args.vendor
        if result.errors:
            source_metadata["errors"] = result.errors

        source_document = models.SourceDocument(
            file_name=file_path.name,
            file_type=processor.name,
            storage_path=str(copied_path.resolve()),
            ingest_started_at=ingest_started_at,
            status="pending",
            extra=source_metadata,
        )
        session.add(source_document)
        session.flush()

        service = OfferIngestionService(session)
        persisted_offers: list[models.Offer] = []
        if result.offers:
            persisted_offers = service.ingest(
                result.offers,
                vendor_name=args.vendor,
                source_document=source_document,
            )
            if persisted_offers:
                source_document.vendor_id = persisted_offers[0].vendor_id
            source_document.status = "processed_with_warnings" if result.errors else "processed"
            print(f"Persisted {len(result.offers)} offers from {file_path.name}")
        else:
            source_document.status = "failed"
            print("No offers extracted; aborting persistence")

        source_document.ingest_completed_at = datetime.now(timezone.utc)
        session.flush()

    return 0 if result.offers else 2


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
