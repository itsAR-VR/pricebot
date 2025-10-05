# QA Checklist – Upload → Ingest → Chat

Use this checklist when validating end-to-end chat functionality after setting up the virtual environment.

## Pre-requisites
- Local PostgreSQL with latest migrations applied (`alembic upgrade head`).
- Seeded dataset containing at least one vendor and product (e.g., run existing fixture script or import `sampleData.temp`).
- `.env` or environment variables configured with:
  - `OPENAI_API_KEY` (only required if testing OCR pathways).
  - `INGESTION_STORAGE_DIR` pointing to a writable directory.

## Happy Path Scenario
1. Start the API server:
   ```bash
   source .venv/bin/activate
   uvicorn app.main:app --reload
   ```
2. Navigate to `/chat` in the browser.
3. Ask: `Find best price for iPhone 17 Pro in Miami`.
   - Expect the new React UI to display progress steps (resolve → best_price → format).
   - Validate that a bundle renders with best + alternate offers.
4. Use the "Download template" quick action to fetch `vendor_price_template.xlsx`, then upload the completed sheet (or `SB Technology Pricelist Oct 25.xlsx`).
   - Provide vendor name via the attachment tray prompt or `@VendorName` mention.
   - Confirm the progress pill reflects upload + ingestion completion.
   - Verify offers from the new document appear after re-running the prompt.

## Edge Cases
- Missing vendor: upload without specifying vendor—UI should block send and show error banner.
- Stale data: query for a product without recent offers—UI should surface "No active offers" summary.
- Vendor filter via `@` mention: ensure the resolved vendor matches `/vendors?q=` search results.
- Multi-file upload: attach 2 spreadsheets, ensure document polling renders each status chip.

## API Spot Checks
- `POST /chat/tools/products/resolve` returns pagination metadata (`total`, `next_offset`).
- `POST /chat/tools/offers/search-best-price` honors filters (vendor, min_price, captured_since).
- `GET /documents/{id}` reflects ingestion status updates post-upload.

## Regression Tests (Manual for Now)
- `/upload` form still works separately from the chat attachment flow.
- Operator dashboard (`/admin/documents`) shows the newly ingested document with offer counts.

## Follow-ups
- Resolve local pip SSL interception (`OSStatus -26276`) so that `.venv` installs complete without manual flags.
- Add automated integration tests once the CI environment can provision the DB and sample files.
- Replace the placeholder `tests/test_integration_upload_chat.py` skip with a real scenario seeded via fixtures.
- Wire Server-Sent Events streaming once backend endpoints expose progress notifications.
