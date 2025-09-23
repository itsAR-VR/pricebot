# Ingestion Playbook

This guide covers day-to-day ingestion tasks for spreadsheets, WhatsApp transcripts, and OCR-able documents.

## 1. Preparing Source Files
- **Spreadsheets**: Save as `.xlsx`, `.xls`, or `.csv`. Ensure price columns include currency tokens or symbols.
- **WhatsApp transcripts**: Export the chat as text (`.txt`) with media omitted. Place the file inside the storage directory (`storage/` locally or `/data/storage` on Railway).
- **Images / PDFs**: Supported formats include `.png`, `.jpg`, `.jpeg`, `.webp`, `.tif`, `.tiff`, and `.pdf`. For PDFs the text layer is extracted; for images we use Tesseract OCR (enable extras via `pip install -e .[ocr,pdf]`).

## 2. Local CLI Commands
```bash
# Spreadsheet ingestion
python -m app.cli.ingest path/to/list.xlsx --vendor "Vendor Name"

# WhatsApp text
python -m app.cli.ingest WAbot/whatsapp_business_chat_data.txt --processor whatsapp_text

# OCR document
python -m app.cli.ingest path/to/offer.png --processor document_text --vendor "Warehouse"
```

Each run copies the artefact into `INGESTION_STORAGE_DIR`, creates a `source_documents` record, and persists offers with links back to the originating document.

## 3. Reviewing Ingestion Output
- JSON APIs: `GET /documents`, `GET /documents/{id}`.
- Operator UI: navigate to `http://localhost:8000/admin/documents` (or the production domain) for a dashboard view.
- CLI overview: `python -m app.cli.list_documents --limit 20`.

## 4. Handling Failures
1. Check the `/documents` detail view for parse errors stored in `extra.errors`.
2. Fix the underlying source (or adjust processor options) and rerun the CLI. Re-ingesting will create a new document snapshot.
3. If a vendor consistently uses a noisy format, consider adding a custom processor or augmenting `text_utils` heuristics.

## 5. Automation Tips
- Combine the CLI with cron/CI jobs (`railway run ...`) to schedule recurring imports.
- Store vendor-specific files in predictable locations (e.g. `/data/storage/vendors/<name>/latest.xlsx`) so scheduled jobs can re-use the same command.
- For WhatsApp, set up a nightly export via WhatsApp Business API or alternate automation and drop the file into the storage directory prior to the scheduled job.

## 6. Post-Ingestion Steps
- Verify new offers in `/offers` (filter by `vendor_id` or `product_id`).
- Tag or categorize new products via the operator UI if they need manual normalization.
- Export snapshots from `price_history` to feed BI tooling if required (`GET /price-history/product/{id}`).

Reach out to engineering before modifying processors under `app/ingestion/`—this code is covered by regression tests and shared by all ingestion jobs.
