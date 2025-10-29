# Ingestion Playbook

This guide covers day-to-day ingestion tasks for spreadsheets, WhatsApp transcripts, and OCR-able documents.

## 1. Preparing Source Files
- **Spreadsheets**: Save as `.xlsx`, `.xls`, or `.csv`. Use the standard header set (`MODEL/SKU`, `DESCRIPTION`, `PRICE`, `QTY`, `CONDITION`) with numeric values only for price/quantity (no currency symbols). A ready-to-use workbook ships at `/documents/templates/vendor-price`; see `docs/CHAT_INTERFACE_SPEC.md#spreadsheet-template-guidelines` for details.
- **WhatsApp transcripts**: Export the chat as text (`.txt`) with media omitted. Place the file inside the storage directory (`storage/` locally or `/data/storage` on Railway).
- **Images / PDFs**: Supported formats include `.png`, `.jpg`, `.jpeg`, `.webp`, `.tif`, `.tiff`, and `.pdf`. Text-first PDFs are parsed directly; scanned paperwork and photos are routed through the GPT vision/OCR pipeline when `ENABLE_OPENAI=true` with `OPENAI_API_KEY` configured (falls back to heuristics if disabled).

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
3. If a vendor consistently uses a noisy format, enable the LLM normalization pipeline (`ENABLE_OPENAI=true`) or add processor-specific overrides via the `prefer_llm` ingestion context flag.

## 5. Automation Tips
- Combine the CLI with cron/CI jobs (`railway run ...`) to schedule recurring imports.
- Store vendor-specific files in predictable locations (e.g. `/data/storage/vendors/<name>/latest.xlsx`) so scheduled jobs can re-use the same command.
- For WhatsApp, set up a nightly export via WhatsApp Business API or alternate automation and drop the file into the storage directory prior to the scheduled job. To force LLM extraction for especially messy chats, pass `--option prefer_llm=true` to the CLI or set `prefer_llm` in the ingestion context.

## 6. Post-Ingestion Steps
- Verify new offers in `/offers` (filter by `vendor_id` or `product_id`).
- Tag or categorize new products via the operator UI if they need manual normalization.
- Export snapshots from `price_history` to feed BI tooling if required (`GET /price-history/product/{id}`).

Reach out to engineering before modifying processors under `app/ingestion/`—this code is covered by regression tests and shared by all ingestion jobs.

## 7. WhatsApp Live Ops
### 7.1 Environment checklist
- **Backend:** populate `WHATSAPP_INGEST_TOKEN` (required) and `WHATSAPP_INGEST_HMAC_SECRET` (optional). Tune `WHATSAPP_INGEST_RATE_LIMIT_PER_MINUTE`, `_BURST`, `WHATSAPP_CONTENT_HASH_WINDOW_HOURS`, `WHATSAPP_EXTRACT_DEBOUNCE_SECONDS`, and `WHATSAPP_INGEST_SIGNATURE_TTL_SECONDS` (default 300 s skew allowance).
- **Collector:** set `WHATSAPP_INGEST_URL`, `WHATSAPP_INGEST_TOKEN`, `CLIENT_ID`, `BATCH_MAX_MESSAGES`, `BATCH_FLUSH_INTERVAL_MS`, and persist `AUTH_STATE_DIR` (defaults to `./auth-state`; on Railway mount a volume and point it at `/data/auth-state`).
- **Vendor mapping:** after a chat first appears, map it to a vendor in `/admin/whatsapp/{chat_id}` so downstream offers are attributed correctly.

### 7.2 Token & HMAC rotation
1. Generate the next token/secret pair and schedule a short maintenance window (five minutes is usually sufficient).
2. Update the backend environment (`WHATSAPP_INGEST_TOKEN` and/or `WHATSAPP_INGEST_HMAC_SECRET`) and redeploy. When the app comes back, hit `GET /metrics/whatsapp` and confirm `totals.auth_failures` and `totals.signature_failures` stay at `0` while `generated_at` advances. Run the signed smoke test in §7.5 to ensure `/chat/tools/diagnostics` reflects the new request.
3. Roll the new credentials to collectors (extension config, Node service, or n8n). Restart each client to pick up the change.
4. Verify every collector can ingest before removing the retired token from circulation. Watch the `/metrics` `recent_failures` array—any remaining `401`/`403` entries identify lagging client IDs. If a collector lags, temporarily raise `WHATSAPP_INGEST_SIGNATURE_TTL_SECONDS` to give operators extra time.
5. Rotation is zero-downtime if collectors ship the new token immediately after the backend update; otherwise they will receive `401/403` responses until reconfigured. Keep rotating creds until `totals.auth_failures` and `totals.forbidden` settle back to zero.

### 7.3 Batch, retry, and backoff tuning
- The collector batches up to `BATCH_MAX_MESSAGES` and flushes every `BATCH_FLUSH_INTERVAL_MS`. Lower the batch size or interval when 429s appear; increase both when throughput is low and the backend is under capacity.
- Delivery uses `p-retry` (three attempts, exponential factor 2, delays between 1–5 s). `429` responses expose `Retry-After`; respect it before resubmitting.
- Backend limits are governed by `WHATSAPP_INGEST_RATE_LIMIT_PER_MINUTE` and `_BURST`. Align collector batching with these values so each client ID stays inside its bucket.
- Persistent `429` or `5xx` spikes are good alert candidates (see §8) and usually signal either collector storms or downstream latency.

### 7.4 QR/session recovery
- The collector stores session keys under `AUTH_STATE_DIR`. When WhatsApp invalidates a session:
  1. Stop the collector (`Ctrl+C` locally or `railway service stop whatsapp-collector`).
  2. Delete the contents of the auth directory (`rm -rf auth-state/*` on a dev machine or the mounted `/data/auth-state` volume).
  3. Restart the collector and scan the QR code from the console output. The status server at `http://collector-host:PORT/healthz` should flip back to `{ "status": "ok" }` once authenticated.
- Preserve the auth directory between deploys; wiping it unnecessarily forces another QR pairing.

### 7.5 Smoke test (signed request)
```bash
SECRET="super-secret"
TOKEN="test-token"
BODY='{"client_id":"smoke","messages":[{"chat_title":"Smoke Chat","text":"Pixel 8 Pro - $750"}]}'
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
SIG=$(python - <<'PY'
import hashlib, hmac, os
secret = os.getenv("SECRET")
ts = os.getenv("TS")
body = os.getenv("BODY").encode("utf-8")
message = ts.encode("utf-8") + b"." + body
print(hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest())
PY
)
curl -X POST http://localhost:8000/integrations/whatsapp/ingest   -H "Content-Type: application/json"   -H "X-Ingest-Token: $TOKEN"   -H "X-Signature: $SIG"   -H "X-Signature-Timestamp: $TS"   -d "$BODY"
```

### 7.6 Auto-extraction
- Successful ingests enqueue `POST /integrations/whatsapp/chats/{chat_id}/extract-latest` after the debounce window. Use `/admin/whatsapp/{chat_id}` to trigger full reruns when mapping changes, and `/chat/tools/diagnostics` to confirm `extracted` counters move.
- For heavily trafficked chats, consider increasing `WHATSAPP_EXTRACT_DEBOUNCE_SECONDS` so extractions run in larger batches instead of thrashing.

### 7.7 Troubleshooting
- `401 invalid ingest token`: the collector still uses a retired token—update configuration and restart.
- `403 invalid signature`: recompute the HMAC with the canonical payload (`timestamp + "." + body`), double-check that the timestamp is UTC within the TTL window, and ensure JSON serialization matches the backend.
- `429 ingest rate limit exceeded`: honour `Retry-After`, then lower batch size/flush interval or raise the backend bucket limits.
- `connection_status=closed` on the collector `/metrics` endpoint: WhatsApp closed the socket. Restart the process and, if it recurs, purge the auth state and rescan the QR code.
- Use the `request_id` returned by the ingest API plus the collector logs to trace failed batches end-to-end.

### 7.8 Ingress IP allowlist
Our ingress controller (Railway or the reverse proxy in front of FastAPI) can enforce an IP allowlist to keep tokens from being replayed by third parties.
1. Collect the outbound IPs for each collector location (SIM banks, data centres, or VPN egress points).
2. Add the addresses/CIDRs to the platform-specific allowlist (Railway: `railway domains allowlist set ...`; Cloudflare: WAF rule → “Known collector IPs”).
3. Update the runbook in your team’s wiki with the current list. Store it alongside the collector deployment manifest so operators rotate both token and IP entries together.
4. When onboarding a new collector, add its IP before distributing credentials. If the collector moves, clear out the old IP right after verifying the new endpoint in `/metrics/whatsapp`.

Keep at least two IPs per region so one collector can fail over without opening the firewall during an incident.

## 8. Observability & Alerting
- **Backend metrics:** `GET /metrics` (or `/metrics/whatsapp`) now returns totals, per-chat counters, and a `recent_failures` feed. Keys of interest:
  - `totals.accepted/created/deduped/extracted` – ingestion throughput.
  - `totals.auth_failures` (401), `totals.forbidden` (403/HMAC), `totals.rate_limited` (429), `totals.signature_failures` (invalid/stale signatures).
  - `recent_failures[*]` identify the most recent 50 auth/rate-limit/media failures by `client_id` and `chat_title`.
- **Backend diagnostics:** `/chat/tools/diagnostics` still returns detailed counters (and CSV + log downloads) for UI consumption.
- **Collector status:** each collector exposes `http://<host>:<PORT>/healthz` and `/metrics`. Scrape both alongside the backend `/metrics` endpoint into Grafana/Datadog (JSON API datasource works out of the box).
- **Dashboards:** import `docs/observability/whatsapp_grafana_dashboard.json` (see §9) to visualise ingest throughput, dedupe ratio, extraction lag, media success, and 401/403/429 trends. Pair it with a second dashboard that plots collector `connection_status`, `batches_success`, and `batches_failed`.
- **Suggested alerts (default thresholds):**
  - `totals.rate_limited` delta ≥ 5/min for 3 consecutive minutes → notify backend + collector owners.
  - `totals.auth_failures` or `totals.forbidden` delta ≥ 3/min → likely stale tokens or HMAC drift.
  - Extraction error ratio (`totals.errors / max(totals.extracted,1)`) ≥ 5 % over 5 minutes → route to ingestion triage.
  - Collector `/metrics.connection_status != "open"` for >2 minutes or no `batches_success` increment in 10 minutes → ask ops to rescan QR/restart.
  - Media failure spike: backend `totals.media_failed` delta ≥ 10 in 5 minutes → investigate storage caps or upstream format changes.
- **Synthetic harness:**
  - `python scripts/whatsapp_harness.py smoke --url <...>/integrations/whatsapp/ingest --token $WHATSAPP_INGEST_TOKEN --diagnostics-url <...>/chat/tools/diagnostics --expect-created 1 --expect-extracted 1 --report-file tmp/smoke.json` runs the signed smoke test, polls diagnostics until the created/extracted counters move, and fails (`exit 2`) if the pipeline does not produce offers within the timeout window.
  - `python scripts/whatsapp_harness.py load --url ... --token ... --count 50 --batch-size 10 --diagnostics-url .../chat/tools/diagnostics --report-file docs/perf_baseline.json` replays batches, enforces the SLOs (p95 latency, skipped ratio, extracted ratio), and emits a JSON report that the nightly job archives.
  - Harness exit codes: `0` success, `2` diagnostics expectation failed, `3` SLO breach(es). Treat non-zero as a failed deploy/nightly run and pipe the stderr “ALERT” line into Slack/PagerDuty with a link to the JSON report for context.
  - Record the latest p50/p95 ingest-to-offer latency, dedupe ratio, extraction success %, and 429 counts in `docs/observability/perf_baseline.md` after each load run. Keep the most recent JSON report checked in at `docs/perf_baseline.json`.

## 9. Dashboard assets
- `docs/observability/whatsapp_grafana_dashboard.json` – ready-to-import Grafana dashboard (JSON API datasource).
- `docs/observability/perf_baseline.md` – update after each load run with new p50/p95 figures and dedupe ratios.
