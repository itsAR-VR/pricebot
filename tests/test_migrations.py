from sqlalchemy import create_engine, inspect, text

from app.db.migrations import run_schema_migrations


def test_run_schema_migrations_adds_missing_columns(tmp_path):
    db_path = tmp_path / "legacy.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE source_documents (
                    id TEXT PRIMARY KEY,
                    vendor_id TEXT,
                    file_name TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    storage_path TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE offers (
                    id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    vendor_id TEXT NOT NULL,
                    price FLOAT NOT NULL,
                    currency TEXT NOT NULL,
                    captured_at DATETIME NOT NULL
                )
                """
            )
        )

    run_schema_migrations(engine)

    inspector = inspect(engine)
    source_columns = {col["name"] for col in inspector.get_columns("source_documents")}
    assert {"ingest_started_at", "ingest_completed_at", "status", "extra"}.issubset(source_columns)

    offer_columns = {col["name"] for col in inspector.get_columns("offers")}
    assert "source_document_id" in offer_columns
