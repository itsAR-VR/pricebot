# Project Plan — Pricebot Roadmap & Backlog

This plan summarizes what’s done, what remains, and the concrete work items (P0/P1/P2) for subagents to pick up. It aligns with AGENTS.md and the chat UX spec.

References:
- Roadmap overview: `docs/project_scope.md`
- Agent working guide: `AGENTS.md`
- Chat UX + tools: `docs/CHAT_INTERFACE_SPEC.md`
- API surfaces: `docs/API_REFERENCE.md`

## Current State
- Core ingestion processors: spreadsheets, WhatsApp text, OCR/PDF (with LLM fallback when enabled)
- Data model: vendors, products, aliases, offers, price_history, source_documents, ingestion_jobs
- APIs: offers, products, vendors, price-history, documents, chat tool endpoints
- Operator UI: upload, documents dashboard, basic chat prototype
- Tests: unit + API tests; one E2E test stub is skipped pending fixtures

See also: `README.md` Current Status; `tests/` suite

## Open Gaps (from roadmap/specs)
- Chat orchestration service (tool planning, guardrails)
- Async ingestion notifications (SSE/WebSocket)
- Background jobs + job status API (use `ingestion_jobs` table)
- Operator UI auth + API key support + basic rate limiting
- Object storage for raw artefacts (persist external to local FS)
- End-to-end integration tests (upload → ingest → chat answer)
- Operational runbooks (SLOs, escalation)

Pointers in repo:
- Spec checkboxes: `docs/CHAT_INTERFACE_SPEC.md:104`
- Security gaps: `DEPLOYMENT_READY.md:187`
- E2E test skip: `tests/test_integration_upload_chat.py:3`

## Backlog by Priority

### P0 — Current Sprint (2 weeks)
1) Background ingestion jobs + status API
   - Create a simple queue worker (prefer RQ for now); enqueue on `/documents/upload` instead of inline processing.
   - Persist to `ingestion_jobs` with `queued/running/succeeded/failed` and timestamps; expose:
     - `GET /documents/jobs/{id}` → status + summary
     - `GET /documents/{doc_id}` shows job(s) linked to document
   - Acceptance:
     - Upload returns `202 Accepted` with `job_id` and `document_id`
     - Polling endpoint returns `running → processed(_with_warnings)/failed`
     - Tests cover happy path and failure path
   - Touch: `app/api/routes/documents.py`, `app/db/models.py`, new `app/services/jobs.py`

2) Async notifications to chat (SSE)
   - Add `GET /chat/stream?conversation_id=` Server‑Sent Events that emits job updates for related documents.
   - Operator chat page connects via EventSource and updates progress breadcrumbs.
   - Acceptance: SSE emits structured events; UI renders state transitions; falls back to polling on lack of SSE.
   - Touch: new `app/api/routes/stream.py`, `app/templates/chat.html`

3) Operator UI authentication
   - Protect `/admin/*` with HTTP Basic Auth behind a flag (`ADMIN_USERNAME/ADMIN_PASSWORD`).
   - Acceptance: 401 without credentials; passes with valid env; disabled in `ENVIRONMENT=local`.
   - Touch: `app/ui/views.py`, new `app/core/auth.py`

4) API key support for chat tools + minimal rate limiting
   - Require `X-API-Key` when `PRICEBOT_API_KEY` is set; return 401 otherwise.
   - Add simple in‑memory limiter (per‑IP/per‑key) for `/chat/tools/*`.
   - Acceptance: key-gated endpoints; 429 on burst; tests for both paths.
   - Touch: middleware in `app/main.py`, new `app/core/security.py`

5) E2E integration test enablement
   - Seed deterministic fixtures (products/vendors/offers) and unskip `test_integration_upload_chat`.
   - Add scenario: upload sample sheet → resolve products → best-price bundles.
   - Acceptance: test passes locally on SQLite and in CI.
   - Touch: `tests/test_integration_upload_chat.py`, `tests/conftest.py`, `tests/fixtures/*`

### P1 — Next
6) Object storage for source_documents
   - Abstract storage writes to support S3/Railway; store URI in `SourceDocument.storage_path`.
   - Env: `OBJECT_STORAGE_URL` or S3 creds; local FS remains default.
   - Acceptance: upload stores to object storage when configured; documents are retrievable via signed URL route.
   - Touch: new `app/services/storage.py`, `app/api/routes/documents.py`

7) Alias management UI + APIs
   - CRUD for `product_aliases`, attach to vendors; add UI under `/admin/aliases`.
   - Acceptance: create/edit/delete alias; affects `products.resolve` results.
   - Touch: `app/api/routes/products.py`, `app/ui/templates/*`, `app/ui/views.py`

8) Price history materialization hardening
   - Close spans on price change; ensure uniqueness constraint holds; add tests for edge cases.
   - Touch: `app/services/offer_ingestion.py` (or equivalent), `app/db/models.py`, tests

### P2 — Later
9) Chat orchestration service
   - Skeleton endpoint `/chat/answer` that sequences: resolve → best‑price → documents.related; plug LLM later.
   - Acceptance: returns structured answer cards per spec without LLM dependency.
   - Touch: new `app/services/orchestrator.py`, `app/api/routes/chat_answer.py`

10) Webhooks + integrations
   - Vendor notifications, Slack/WhatsApp outbound hooks on notable changes.
   - Acceptance: configurable webhook targets, signed payloads.

11) Ops: runbooks + dashboards
   - Write SLOs, on‑call playbook; add `/chat/tools/diagnostics` export to Grafana/ELK later.

## Issue Templates (copy to GitHub)
- P0: Background ingestion jobs (+status API) — owner: Backend
- P0: SSE stream for chat progress — owner: Backend/UI
- P0: Protect /admin with Basic Auth — owner: Backend
- P0: API key + rate limiting for /chat/tools — owner: Backend
- P0: Enable E2E upload→chat test — owner: QA
- P1: Object storage adapter + signed URLs — owner: Backend
- P1: Alias management UI — owner: UI
- P1: Price history span hardening — owner: Backend
- P2: Chat orchestration endpoint (non‑LLM) — owner: Backend
- P2: Webhook integrations — owner: Backend
- P2: Ops runbooks — owner: DevOps

Each issue should include: scope, acceptance criteria, file pointers, test plan.

## Notes for Subagents
- Keep work scoped to `app/` (see `AGENTS.md`).
- Update `docs/API_REFERENCE.md` and this file when endpoints or flows change.
- Add/adjust tests near the code you touch; avoid flakey time‑dependent assertions.

Last updated: automated by plan sync.

