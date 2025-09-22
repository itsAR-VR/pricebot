# Pricebot Backend

Backend service for Cellntell's price intelligence platform. The service ingests pricing sheets from vendors (Excel, CSV, PDF, images, chat dumps), normalizes the information, and exposes APIs that surface the latest and historical price data per product and vendor.

## Core Features (Roadmap)
- Structured ingestion for spreadsheets (Excel/CSV) with automatic schema detection.
- OCR + LLM assisted extraction for PDFs and images shared via WhatsApp.
- Persistent storage of vendors, products, and offer history (PostgreSQL/SQLite).
- Retrieval APIs for current price, history, and document traceability.
- Embedding-based search across aliases and document payloads.
- Chatbot-friendly query layer for Vercel frontend integration.

## Local Development
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload
```

### CLI Ingestion
Parse a spreadsheet and persist offers into the local database:
```bash
python -m app.cli.ingest "../Raw Data from Abdursajid.xlsx" --vendor "Raw Vendor"
```

The default configuration uses a local SQLite database (`pricebot.db`). Override settings via environment variables as defined in `app/core/config.py`.

## Repository Layout
- `app/core`: configuration helpers.
- `app/db`: SQLModel definitions and session utilities.
- `app/api`: FastAPI routers and dependencies.
- `app/ingestion`: modular ingestion processors that translate raw files into normalized offers.
- `app/services`: higher-level business logic for persisting and querying data.
- `docs/`: design documents, including the schema draft.

