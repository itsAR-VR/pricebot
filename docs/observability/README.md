# Observability Overview

This folder groups the artefacts that power the F/H observability commitments for WhatsApp ingest.

## 1. Backend telemetry
- `GET /metrics` → `{ "whatsapp": { "totals": {...}, "counters": [...], "recent_failures": [...] } }`.
- `GET /metrics/whatsapp` → same payload without the service wrapper.
- Key totals:
  - `accepted`, `created`, `deduped`, `extracted`, `errors`.
  - `media_uploaded`, `media_deduped`, `media_failed`.
  - `auth_failures` (401 invalid token/missing signature), `forbidden` (403 HMAC issues), `rate_limited` (429), `signature_failures`.
- `recent_failures` contains the last 50 auth/token/rate-limit/media failures with timestamps to speed up triage.
- Use the FastAPI `/metadata` endpoint to tag datasource instances (e.g. environment name) when piping the JSON into Grafana/Datadog.

## 2. Dashboards
- Import `whatsapp_grafana_dashboard.json` into Grafana. It assumes the JSON API datasource plugin (`simpod-json-datasource`) and points to `/metrics`.
- Recommended layout:
  1. Stats for created/deduped offers, extraction errors, media failures.
  2. Time series for 401/403/429 trends.
  3. Table of `recent_failures` with `client_id`, `chat_title`, and `reason`.
- Pair it with collector dashboards that plot `/metrics` output from each Node collector (batch successes/failures, connection status).

## 3. Alerts
- **401/403 spikes:** delta ≥ 3/min for 3 minutes. Page ops to rotate tokens/HMAC.
- **429 spikes:** delta ≥ 5/min for 3 minutes. Notify backend + collector owners to tune rate limits or reduce batch size.
- **Extraction failures:** `errors / max(extracted, 1) ≥ 0.05` for 5 minutes. Route to ingestion triage.
- **Collector offline:** collector `/metrics.connection_status != "open"` for >2 minutes OR no `batches_success` increment in 10 minutes. Wake collector owners.
- **Media failures:** backend `media_failed` delta ≥ 10 in 5 minutes. Inspect storage quotas and media formats.
- Wire alerts to Slack (#pricebot-ops) and PagerDuty (Pricebot ingest) with clear runbook links (§7 of `ingestion_playbook.md`).

## 4. Performance baseline
- Run `python scripts/whatsapp_harness.py load --url <staging-url>/integrations/whatsapp/ingest --token $WHATSAPP_INGEST_TOKEN --diagnostics-url <staging-url>/chat/tools/diagnostics --count 50 --batch-size 10 --report-file docs/perf_baseline.json` on each nightly job.
- The harness enforces SLOs inline (p95 ingest→offer < 60 s, skipped/error ratio < 2 %, extracted/created ≥ 95 %). It exits non-zero and prints an `ALERT:` stanza if any threshold is breached.
- Commit the generated `docs/perf_baseline.json` and copy the key figures (p50, p95, extraction success %, dedupe ratio, 429 counts) into `perf_baseline.md`. Keep at least the last three entries for trend analysis.
- When a run slips outside SLOs, raise an incident stub linking to the failing JSON report and document mitigation + follow-up tasks.

## 5. Integrations
- Grafana: JSON API datasource hitting `/metrics` every 15 s; use table transform to flatten `recent_failures`.
- Datadog: scrape `/metrics` with an HTTP check, forward fields as gauges (`pricebot.whatsapp.created`, `...rate_limited`, etc.), and attach monitors matching the thresholds above.
- Railway monitors: configure HTTP checks for `/metrics` (200 OK) and custom scripts that assert `rate_limited` or `auth_failures` deltas stay under thresholds.
