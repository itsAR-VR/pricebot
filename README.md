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
Parse source files and persist offers into the local database:
```bash
# Excel/CSV
python -m app.cli.ingest "../Raw Data from Abdursajid.xlsx" --vendor "Raw Vendor"

# WhatsApp text export (force the chat parser)
python -m app.cli.ingest WAbot/whatsapp_business_chat_data.txt --processor whatsapp_text

# Image or PDF price sheet (requires `pip install -e .[ocr,pdf]`)
python -m app.cli.ingest vendor_sheet.png --processor document_text --vendor "Sample Vendor"

# Review ingested documents
python -m app.cli.list_documents --limit 20
```

List registered processors:
```bash
python - <<'PY'
from app.ingestion import registry
print(sorted(registry.processors))
PY
```

The default configuration uses a local SQLite database (`pricebot.db`). Override settings via environment variables as defined in `app/core/config.py`.

### Testing
```bash
pip install -e .[dev]
pytest
```

### Deployment
- `Procfile` runs `uvicorn app.main:app` (compatible with Railway/Heroku dynos).
- `railway.json` configures the Railway service with health checks hitting `/health`.
- Set environment variables (`DATABASE_URL`, `OPENAI_API_KEY`, etc.) through your hosting platform.
- See `docs/deployment_railway.md` for a complete Railway rollout and scheduling guide.

### Operator UI
- Visit `http://localhost:8000/admin/documents` to monitor artefacts, statuses, and extracted offers.
- Filter by status, drill into document details, and inspect raw ingestion metadata.
- In production the same console is available at `https://<your-domain>/admin/documents` (protect with your platform’s auth solution).

## Repository Layout
- `app/core`: configuration helpers.
- `app/db`: SQLModel definitions and session utilities.
- `app/api`: FastAPI routers and dependencies.
- `app/ingestion`: modular ingestion processors that translate raw files into normalized offers.
- `app/services`: higher-level business logic for persisting and querying data.
- `docs/`: design documents, including the schema draft.
