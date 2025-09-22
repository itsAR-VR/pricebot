from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.session import get_session, init_db
from app.ingestion.base import registry
from app.services.offers import OfferIngestionService


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest a pricing spreadsheet into the database")
    parser.add_argument("file", type=Path, help="Path to the spreadsheet (.csv, .xls, .xlsx)")
    parser.add_argument("--vendor", required=False, help="Override vendor name")
    parser.add_argument("--currency", required=False, help="Override currency code")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    file_path: Path = args.file
    if not file_path.exists():
        print(f"File not found: {file_path}")
        return 1

    processor = registry.match(file_path)
    if not processor:
        print(f"No processor registered for file type: {file_path.suffix}")
        return 1

    context: dict[str, Any] = {
        "vendor_name": args.vendor,
        "currency": args.currency or settings.default_currency,
    }

    result = processor.process(file_path, context=context)
    if result.errors:
        print("Encountered errors during parsing:")
        for error in result.errors:
            print(f" - {error}")
    if not result.offers:
        print("No offers extracted; aborting persistence")
        return 2

    init_db()
    with get_session() as session:
        service = OfferIngestionService(session)
        service.ingest(result.offers)
        print(f"Persisted {len(result.offers)} offers from {file_path.name}")

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
