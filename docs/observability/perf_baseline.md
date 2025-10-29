# WhatsApp Ingestion Performance Baseline

Track the latest performance runs here. Update after each load harness execution so regressions are easy to spot.

## How to update
1. Run the load harness (nightly): `python scripts/whatsapp_harness.py load --url <staging-url>/integrations/whatsapp/ingest --token $WHATSAPP_INGEST_TOKEN --diagnostics-url <staging-url>/chat/tools/diagnostics --count 50 --batch-size 10 --report-file docs/perf_baseline.json`.
2. Inspect the JSON report that the harness writes to `docs/perf_baseline.json` (it mirrors the CLI summary and SLO verdicts). Copy the headline figures below.
3. Capture the backend `/metrics` snapshot immediately after the run (store alongside the Jenkins/Actions artefacts) so spikes are traceable.
4. Keep at least the last three data points in this Markdown table; older entries can move to an archive if this file grows unwieldy.

## Latest runs

| Date (UTC) | Environment | Messages replayed | Media attachments | p50 ingest→offer (s) | p95 ingest→offer (s) | Extract success % | Dedupe ratio % | 429s / min | Notes |
|------------|-------------|-------------------|-------------------|----------------------|----------------------|-------------------|----------------|------------|-------|
| 2025-10-28 | staging     | 200               | 0                 | 0.935                | 1.080                | 95.3              | 15.0           | 0          | Harness load run (20×10). SLOs green. |
