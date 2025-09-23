# Price Intelligence Platform Roadmap

## Vision
Deliver a durable market intelligence system for Cellntell that continuously ingests vendor price lists (spreadsheets, chat exports, PDFs, and images), normalizes products, stores historical trends, and exposes both an API and conversational interface for internal teams and clients.

## Phased Plan

### Phase 1 – Structured Ingestion (Current)
- ✅ Spreadsheet ingestion (Excel/CSV/XLS) with automatic schema detection.
- ✅ SQLite backing store for rapid prototyping; SQLModel models ready for PostgreSQL.
- ✅ CLI workflow to load vendor price sheets and other sources.
- ✅ REST endpoints for price lookups, price history, and vendor/product browsing.
- ✅ Basic operator UI to review ingested rows, resolve duplicates, and monitor failed rows.

### Phase 2 – Semi/Unstructured Sources
- ✅ WhatsApp chat parser (regex heuristics) for `.txt` exports.
- ✅ OCR-backed document processor (pytesseract/pypdf) for JPEG/PDF price sheets.
- Persist original artifacts to object storage (Railway bucket or S3) and link via `source_documents` table.
- Introduce retryable ingestion jobs with status tracking and logging via `ingestion_jobs`.

### Phase 3 – Intelligence & Query Layer
- Surface `/prices/{product}` and `/vendors/{vendor}` endpoints with filters (condition, location, time range).
- Implement price history materialization logic (close open spans when new offer arrives).
- Add product alias management (LLM embeddings + manual overrides) to improve matching.
- Integrate retrieval API with a chatbot frontend (Vercel) that can answer questions like “Best current price for Pixel 8 128GB?”.

### Phase 4 – Production Hardening
- Move persistence to managed PostgreSQL (Railway), add Alembic migrations.
- Deploy FastAPI on Railway; connect to Vercel frontend via secure API tokens.
- Set up monitoring (health checks, logging aggregation) and automated nightly ingestions.
- Implement RBAC for operator access and audit logging for price edits.

## Data Management
- Canonical entities: `vendors`, `products`, `product_aliases`, `offers`, `price_history`, `source_documents`, `ingestion_jobs`.
- Raw payloads for each offer retained (`offers.raw_payload`) for auditing.
- Timezone-agnostic storage in UTC; capture vendor-local metadata in `source_documents.extra`.
- Planned deduplication rules: prefer SKU/UPC matches, fallback to alias cosine similarity, manual overrides when conflicts persist.

## Tech Choices
- **Backend**: FastAPI + SQLModel; asynchronous endpoints for ingestion triggers.
- **DB**: SQLite for local dev, PostgreSQL in production via SQLModel (async engine optional later).
- **Storage**: Local filesystem (`storage/`) during development; migrate to Railway object storage.
- **LLM/OCR**: Tesseract OCR, optional OpenAI/Anthropic for complex parsing once API keys configured.
- **Deploy**: Vercel frontend, Railway backend; IaC/Terraform considered for repeatable setup.

## Deliverables Checklist
- [x] Repository scaffold with config, ingestion registry, and CLI.
- [x] Data schema documentation (`docs/data_schema.md`).
- [x] Long-term roadmap (this document).
- [ ] API documentation (OpenAPI generated from FastAPI + narrative docs).
- [ ] Operational runbooks (ingestion SLOs, escalation steps).
- [x] Test coverage: unit tests for ingestion heuristics and offer history.

## Next Actions
1. Persist inbound artifacts to object storage and wire `source_documents` references.
2. Harden OCR + chat parsing with LLM fallback and structured prompts.
3. Add integration tests for CLI + API endpoints once Postgres fixture is available.
4. Roll out deployment to Railway/Vercel using `Procfile` and `railway.json`, then monitor and iterate.
5. Extend operator UI with alias management and manual correction tools.
