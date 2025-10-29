from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


def run_schema_migrations(engine: Engine) -> None:
    """Ensure legacy databases have columns required by the current models."""

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    relevant_tables = {"source_documents", "offers", "whatsapp_chats"} & table_names
    if not relevant_tables:
        return

    dialect = engine.dialect.name

    def _timestamp_type() -> str:
        return "DATETIME" if dialect == "sqlite" else "TIMESTAMP"

    def _status_type() -> str:
        return "TEXT" if dialect == "sqlite" else "VARCHAR(50)"

    def _json_type() -> str:
        if dialect == "postgresql":
            return "JSONB"
        if dialect == "sqlite":
            return "TEXT"
        return "JSON"

    def _uuid_type() -> str:
        return "UUID" if dialect == "postgresql" else "TEXT"

    try:
        with engine.begin() as connection:
            if "source_documents" in table_names:
                columns = {col["name"] for col in inspector.get_columns("source_documents")}
                statements: list[str] = []
                post_statements: list[str] = []

                if "ingest_started_at" not in columns:
                    statements.append(
                        f"ALTER TABLE source_documents ADD COLUMN ingest_started_at {_timestamp_type()} NULL"
                    )
                if "ingest_completed_at" not in columns:
                    statements.append(
                        f"ALTER TABLE source_documents ADD COLUMN ingest_completed_at {_timestamp_type()} NULL"
                    )
                status_missing = "status" not in columns
                if status_missing:
                    statements.append(
                        f"ALTER TABLE source_documents ADD COLUMN status {_status_type()} DEFAULT 'pending'"
                    )
                if "extra" not in columns:
                    statements.append(
                        f"ALTER TABLE source_documents ADD COLUMN extra {_json_type()}"
                    )
                if "source_whatsapp_message_id" not in columns:
                    statements.append(
                        f"ALTER TABLE source_documents ADD COLUMN source_whatsapp_message_id {_uuid_type()} NULL"
                    )
                    post_statements.append(
                        "CREATE INDEX IF NOT EXISTS ix_source_documents_source_whatsapp_message_id "
                        "ON source_documents (source_whatsapp_message_id)"
                    )

                for statement in statements:
                    logger.info("Applying migration: %s", statement)
                    connection.execute(text(statement))

                if status_missing or "status" in columns:
                    connection.execute(
                        text("UPDATE source_documents SET status = COALESCE(status, 'pending')")
                    )
                for statement in post_statements:
                    logger.info("Applying migration: %s", statement)
                    connection.execute(text(statement))

            if "offers" in table_names:
                columns = {col["name"] for col in inspector.get_columns("offers")}
                if "source_document_id" not in columns:
                    statement = (
                        f"ALTER TABLE offers ADD COLUMN source_document_id {_uuid_type()} NULL"
                    )
                    logger.info("Applying migration: %s", statement)
                    connection.execute(text(statement))
                if "source_whatsapp_message_id" not in columns:
                    statement = (
                        f"ALTER TABLE offers ADD COLUMN source_whatsapp_message_id {_uuid_type()} NULL"
                    )
                    logger.info("Applying migration: %s", statement)
                    connection.execute(text(statement))

            if "whatsapp_chats" in table_names:
                columns = {col["name"] for col in inspector.get_columns("whatsapp_chats")}
                if "last_extracted_at" not in columns:
                    statement = (
                        f"ALTER TABLE whatsapp_chats ADD COLUMN last_extracted_at {_timestamp_type()} NULL"
                    )
                    logger.info("Applying migration: %s", statement)
                    connection.execute(text(statement))
                if "vendor_id" not in columns:
                    statement = f"ALTER TABLE whatsapp_chats ADD COLUMN vendor_id {_uuid_type()} NULL"
                    logger.info("Applying migration: %s", statement)
                    connection.execute(text(statement))
                    index_stmt = "CREATE INDEX IF NOT EXISTS ix_whatsapp_chats_vendor_id ON whatsapp_chats (vendor_id)"
                    logger.info("Applying migration: %s", index_stmt)
                    connection.execute(text(index_stmt))

    except SQLAlchemyError:
        logger.exception("Schema migration failed")
        raise
