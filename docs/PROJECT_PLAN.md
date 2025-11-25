# Project Plan — Pricebot Roadmap & Backlog

This plan summarizes what's done, what remains, and the concrete work items (P0/P1/P2) for subagents to pick up. It aligns with AGENTS.md and the chat UX spec.

References:
- Roadmap overview: `docs/project_scope.md`
- Agent working guide: `AGENTS.md`
- Chat UX + tools: `docs/CHAT_INTERFACE_SPEC.md`
- API surfaces: `docs/API_REFERENCE.md`

## Current State

**Version:** 2.0 (Feature Expansion)  
**Last Updated:** November 2025

### Completed Features

#### Core Infrastructure (MVP Complete)
- ✅ Core ingestion processors: spreadsheets, WhatsApp text, OCR/PDF (with LLM fallback when enabled)
- ✅ Data model: vendors, products, aliases, offers, price_history, source_documents, ingestion_jobs
- ✅ APIs: offers, products, vendors, price-history, documents, chat tool endpoints
- ✅ Operator UI: upload, documents dashboard, basic chat prototype
- ✅ Tests: unit + API tests

#### Background Processing (Complete)
- ✅ Background ingestion jobs (`app/services/ingestion_jobs.py`)
- ✅ Async notifications/SSE (`app/api/routes/chat_stream.py`)
- ✅ Job status API via IngestionJob model

#### RAG/Semantic Search (Complete - v2.0)
- ✅ numpy dependency added for cosine similarity
- ✅ Embedding backfill script (`scripts/backfill_embeddings.py`)
- ✅ Vector search fallback in `resolve_products` when SQL returns < 3 results
- ✅ Integration with LLM reranking pipeline

#### WhatsApp Integration (Complete - v2.0)
- ✅ Inbound message ingestion (`app/services/whatsapp_ingest.py`)
- ✅ Outbound messaging service (`app/services/whatsapp_outbound.py`)
- ✅ Send endpoint: `POST /integrations/whatsapp/chats/{chat_id}/send`
- ✅ Media storage (Local, S3, GCS) via `app/services/media_storage.py`

#### Chat Orchestration / Negotiation Bot (Complete - v2.1)
- ✅ ChatOrchestrator service (`app/services/chat_orchestrator.py`)
- ✅ Automatic response to inbound messages
- ✅ RAG-powered product lookup for context
- ✅ LLM negotiation brain with fallback responses
- ✅ System prompts for negotiation (`app/core/prompts.py`)
- ✅ Triggered on message ingest via background task

#### P1 Items (Complete - v2.0)
- ✅ Alias management API - Full CRUD at `/products/{id}/aliases`
- ✅ Alias management UI at `/admin/aliases`
- ✅ Price history materialization hardening - proper span closing

See also: `README.md` Current Status; `tests/` suite

## Open Gaps (from roadmap/specs)
- ~~Chat orchestration service (tool planning, guardrails)~~ ✅ DONE
- Operator UI auth + API key support + basic rate limiting
- Object storage for raw artefacts (persist external to local FS) - partially done via media_storage
- End-to-end integration tests (upload → ingest → chat answer)
- Operational runbooks (SLOs, escalation)

Pointers in repo:
- Spec checkboxes: `docs/CHAT_INTERFACE_SPEC.md:104`
- Security gaps: `DEPLOYMENT_READY.md:187`
- E2E test skip: `tests/test_integration_upload_chat.py:3`

## Backlog by Priority

### P0 — Current Sprint (Complete)

1) ✅ Background ingestion jobs + status API
   - RQ-style worker via ThreadPoolExecutor
   - `IngestionJob` model with queued/running/succeeded/failed
   - SSE stream at `/chat/stream` for real-time updates

2) ✅ Semantic Search (RAG)
   - Vector search using OpenAI embeddings
   - Cosine similarity via numpy
   - Fallback when SQL ILIKE returns < 3 results
   - Backfill script for existing aliases

3) ✅ WhatsApp Outbound Messaging
   - `WhatsAppOutboundService` with `send_text` method
   - Mock relay implementation (logs to console)
   - Records messages with `is_outgoing=True`

### P1 — Next (Mostly Complete)

4) ✅ Alias management UI + APIs
   - Full CRUD: create, read, update, delete
   - Bulk create support
   - Filter by embedding status
   - UI at `/admin/aliases`

5) ✅ Price history materialization hardening
   - Proper span closing on price changes
   - Out-of-order insertion handling
   - Uniqueness constraint protection
   - Logging for price change auditing

6) Object storage for source_documents (Partially Done)
   - ✅ Media storage abstraction supports S3/GCS/local
   - TODO: Migrate SourceDocument.storage_path to use abstraction
   - TODO: Signed URL route for document retrieval

7) Operator UI authentication
   - Protect `/admin/*` with HTTP Basic Auth behind a flag (`ADMIN_USERNAME/ADMIN_PASSWORD`).
   - Acceptance: 401 without credentials; passes with valid env; disabled in `ENVIRONMENT=local`.
   - Touch: `app/ui/views.py`, new `app/core/auth.py`

8) API key support for chat tools + minimal rate limiting
   - Require `X-API-Key` when `PRICEBOT_API_KEY` is set; return 401 otherwise.
   - Add simple in‑memory limiter (per‑IP/per‑key) for `/chat/tools/*`.
   - Touch: middleware in `app/main.py`, new `app/core/security.py`

### P2 — Later

9) ✅ Chat orchestration service (DONE - moved to v2.1)
   - ChatOrchestrator with handle_incoming_message
   - LLM-powered negotiation responses
   - Automatic trigger on inbound WhatsApp messages
   - RAG integration for product context

10) Webhooks + integrations
    - Vendor notifications, Slack/WhatsApp outbound hooks on notable changes
    - Configurable webhook targets, signed payloads

11) E2E integration test enablement
    - Seed deterministic fixtures (products/vendors/offers)
    - Add scenario: upload sample sheet → resolve products → best-price bundles
    - Touch: `tests/test_integration_upload_chat.py`, `tests/conftest.py`

12) Ops: runbooks + dashboards
    - Write SLOs, on‑call playbook
    - Add `/chat/tools/diagnostics` export to Grafana/ELK later

## Issue Templates (copy to GitHub)

### Completed
- ✅ P0: Background ingestion jobs (+status API) — Backend
- ✅ P0: SSE stream for chat progress — Backend/UI
- ✅ P0: Semantic Search (RAG) Implementation — Backend
- ✅ P0: WhatsApp Outbound Messaging — Backend
- ✅ P1: Alias management UI + API — Backend/UI
- ✅ P1: Price history span hardening — Backend
- ✅ P2: Chat Orchestration / Negotiation Bot — Backend (v2.1)

### In Progress / Planned
- P1: Protect /admin with Basic Auth — Backend
- P1: API key + rate limiting for /chat/tools — Backend
- P1: Object storage adapter + signed URLs — Backend
- ~~P2: Chat orchestration endpoint (non‑LLM) — Backend~~ ✅ DONE
- P2: Webhook integrations — Backend
- P2: Enable E2E upload→chat test — QA
- P2: Ops runbooks — DevOps

Each issue should include: scope, acceptance criteria, file pointers, test plan.

## Architecture Notes

### Key Services

| Service | Location | Purpose |
|---------|----------|---------|
| `ChatLookupService` | `app/services/chat.py` | Product resolution with SQL + vector search |
| `ChatOrchestrator` | `app/services/chat_orchestrator.py` | Negotiation bot brain - connects ingest to responses |
| `WhatsAppIngestService` | `app/services/whatsapp_ingest.py` | Raw message persistence |
| `WhatsAppOutboundService` | `app/services/whatsapp_outbound.py` | Outbound message handling |
| `OfferIngestionService` | `app/services/offers.py` | Offer persistence + price history |
| `IngestionJobRunner` | `app/services/ingestion_jobs.py` | Background document processing |
| `MediaStorage` | `app/services/media_storage.py` | Object storage abstraction |

### New Endpoints (v2.0)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/integrations/whatsapp/chats/{id}/send` | Send outbound WhatsApp message |
| GET | `/products/{id}/aliases` | List product aliases |
| POST | `/products/{id}/aliases` | Create product alias |
| POST | `/products/{id}/aliases/bulk` | Bulk create aliases |
| PUT | `/products/{id}/aliases/{alias_id}` | Update alias |
| DELETE | `/products/{id}/aliases/{alias_id}` | Delete alias |
| GET | `/products/aliases/all` | List all aliases with filters |
| GET | `/admin/aliases` | Alias management UI |

## Notes for Subagents
- Keep work scoped to `app/` (see `AGENTS.md`).
- Update `docs/API_REFERENCE.md` and this file when endpoints or flows change.
- Add/adjust tests near the code you touch; avoid flakey time‑dependent assertions.
- Run `python scripts/backfill_embeddings.py` after adding aliases to populate embeddings.

Last updated: November 2025 (v2.1 Negotiation Bot)
