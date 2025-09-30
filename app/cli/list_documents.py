from __future__ import annotations

import argparse
from datetime import datetime

from sqlmodel import select

from app.db import models
from app.db.session import get_session, init_db


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List ingested source documents")
    parser.add_argument("--limit", type=int, default=50, help="Maximum records to display")
    parser.add_argument("--status", type=str, help="Filter by status")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    init_db()

    statement = select(models.SourceDocument).order_by(models.SourceDocument.ingest_started_at.desc())
    if args.status:
        statement = statement.where(models.SourceDocument.status == args.status)
    statement = statement.limit(args.limit)

    with get_session() as session:
        documents = session.exec(statement).all()

        if not documents:
            print("No documents found")
            return 0

        headers = ["ID", "File", "Processor", "Status", "Offers", "Started", "Completed"]
        rows = [
            [
                str(document.id),
                document.file_name,
                document.file_type,
                document.status,
                str(len(document.offers or [])),
                _fmt(document.ingest_started_at),
                _fmt(document.ingest_completed_at),
            ]
            for document in documents
        ]

    _print_table(headers, rows)
    return 0


def _fmt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M")


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(row[idx]) for row in ([headers] + rows)) for idx in range(len(headers))]
    header_line = " | ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers))
    separator = "-+-".join("-" * width for width in widths)
    print(header_line)
    print(separator)
    for row in rows:
        print(" | ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)))


if __name__ == "__main__":
    raise SystemExit(main())
