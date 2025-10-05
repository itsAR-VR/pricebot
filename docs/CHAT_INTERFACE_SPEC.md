# Conversational Price Intelligence Experience

## Purpose
Define the end-to-end chat workflow that mirrors Cursor/ChatGPT while remaining tightly integrated with Pricebot's ingestion pipelines and offer database. The experience must let internal users upload vendor artefacts (spreadsheets, PDFs, images, WhatsApp exports), ask natural-language questions, and receive structured answers grounded in the latest offers.

## Core User Flow
1. **Open chat workspace** – Users land on `/chat` (web) with transcript history, system status, and quick actions ("Upload price sheet", "Find best price").
2. **Compose message** – Rich editor supporting:
   - Free text prompts.
   - Drag-and-drop or file picker for spreadsheets, PDFs, images, and `.txt` chats.
   - Inline image previews and file badges before sending.
   - Optional vendor tagging (`@vendor-name`) to scope the search.
3. **LLM orchestration** – GPT-5 orchestrator receives the message payload and runs tool calls as needed (see "Agentic Tooling").
4. **Progress feedback** – Show streaming tokens plus tool-call breadcrumbs ("Searching offers…", "Parsing SB_Technology_Pricelist_Oct25.xlsx…").
5. **Response delivery** – Rich answer card with canonical product info, best offers, supporting metadata, and linked artefacts. Persist chat transcript and attached files for audit.
6. **Follow-up actions** – Quick replies ("Show price history", "Contact vendor") and download/export options (CSV of offers, copy summary).

## Agentic Tooling
The chat orchestration layer exposes deterministic tools. GPT-5 plans when to call them; backend enforces auth and rate limits.

| Tool | Purpose | Input | Output |
| --- | --- | --- | --- |
| `offers.search_best_price` | Retrieve best active price per product. | `{ "query": str, "filters": {"vendor_id?", "condition?", "location?"} }` | List of offers with price, vendor, captured_at, source_document_id. |
| `products.resolve` | Map natural language mention to canonical product ID(s). | `{ "query": str, "hints": ["iphone 17 pro"], "attachments": [...] }` | Product records with alias confidence and spec metadata. |
| `documents.ingest` | Trigger ingestion for newly uploaded files. | `{ "document_id": UUID }` | Status (`queued` → `processed`), extracted offers summary, error payload if failure. |
| `documents.list_related` | Surface artefacts that contributed to the answer. | `{ "offer_ids": [...] }` | File metadata (type, upload_time, uploader, preview URL). |
| `vendors.contact_info` | Resolve vendor communication channels. | `{ "vendor_id": UUID }` | Phone, email, WhatsApp handles, last verified timestamp. |

All tool invocations are logged per chat turn. Fallback logic must gracefully surface structured errors (e.g., ingestion still running) with retry guidance.

## Answer Composition
Each assistant turn answering a price question should render:
- **Primary product card**
  - Canonical name, hero photo (if present), key specs (capacity, color, condition).
  - "Best price" row: price, currency, quantity, vendor name, captured_at (UTC), relative freshness ("3 hours ago").
- **Alternate offers list** (up to 5)
  - Sorted by price ascending.
  - Include condition, warehouse/location, MOQ if available.
- **Vendor details**
  - Contact info (phone, WhatsApp, email) and trust score (if available).
  - Link to open vendor profile in `/admin/documents`.
- **Source artefacts**
  - Thumbnails for images/PDFs; file badges for spreadsheets.
  - Click opens viewer with highlighted row extracted.
- **Audit metadata**
  - When ingestion completed, uploader identity, processing status.

When photos are absent, replace hero image with neutral placeholder but retain spec + offer rows.

## File Upload Handling
- Accept: `.xlsx`, `.xls`, `.csv`, `.pdf`, `.png`, `.jpg`, `.txt` (WhatsApp export), `.zip` (treated as batch; unpack server-side).
- Enforce per-file size limit (50 MB default) and virus scanning before ingestion.
- Upon upload:
  1. Persist raw file in `storage/` (dev) or object storage (prod) and create `source_documents` row with status `pending`.
  2. Emit ingestion job via `documents.ingest` tool → pipeline resolves processor (`excel_tabular`, `pdf_ocr`, etc.).
  3. Stream ingestion state back to chat (status messages). If ingestion is ongoing when user asks a question, GPT-5 should first verify `status == processed` before relying on data.
  4. Errors surface as inline alerts with remediation steps (e.g., "Header row missing—download template" with CTA to view spreadsheet guidelines).

## Spreadsheet Template Guidelines
Leverage existing vendor sheets to define a normalized template that maximizes auto-detection success.

### Required Columns
| Column | Description | Example Source |
| --- | --- | --- |
| `MODEL/SKU` | Vendor or OEM SKU/MPN used for matching. | `RT-IP1164GBBK-UN-RW-FE` (Raw Data from Abdursajid.xlsx) |
| `DESCRIPTION` | Human-readable product name with capacity/color. | `IPHONE 11 64GB BLACK (CDMA/GSM)` |
| `PRICE` | Unit price in USD; no currency symbol. | `485.00` |
| `QTY` | On-hand quantity or MOQ. | `150` |
| `CONDITION` | Grade or status (A/A-, New, Refurb). | `A/A-` |

### Optional Enhancements
| Column | Purpose | Example |
| --- | --- | --- |
| `UPC` | Speed up canonical product mapping. | `518190000047` (SB Technology Pricelist Oct 25.xlsx) |
| `MPN` | Manufacturer part number from SB Technology sheets. | `GA05662-US` |
| `WAREHOUSE` / `LOCATION` | Show fulfillment site or hub. | `Miami Scan` |
| `VENDOR_CONTACT` | Override default contact info per row. | `morris@myiicco.com` |
| `NOTES` | Payment terms, freight, etc. | `Prices valid through 10/31.` |

### Formatting Rules
- Header row must occupy the first non-empty row (no merged cells above). Provide vendor metadata in a separate "Info" tab when possible.
- One product offer per row. Avoid multi-line descriptions inside cells.
- Use plain numbers for `PRICE`/`QTY`; do not include commas, currency symbols, or text (e.g., `"$429"` → `429`).
- Preserve consistent column names across uploads to skip manual mapping.
- For legacy sheets like `Laptop List Sept 10.xls`, normalize before upload by:
  - Clearing promo text rows above the header.
  - Renaming columns to the required schema (`DESCRIPTION`, `PRICE`, `QTY`, etc.).
  - Saving as `.xlsx` to avoid encoding issues.

### Template Distribution
Publish a downloadable template (`storage/templates/vendor_price_template.xlsx`) with the required columns and validation rules. Provide a "Download template" action inside the chat uploader for quick access. The template is served at `/documents/templates/vendor-price` for both UI links and API clients.

## Conversation Guardrails
- GPT-5 must confirm tool outputs before responding (no hallucinated prices).
- If no offers found, respond with graceful fallback: "No current listings for iPhone 17 Pro. Last seen on <date> from <vendor>. Want me to notify you when it appears?"
- Flag stale data when `captured_at` older than 7 days and suggest uploading fresh sheets.
- Respect user permissions: chat view scoped to vendors/regions the user can access.

## Telemetry & Audit
- Log each chat turn with correlation ID linking to `source_documents` and tool calls.
- Store structured transcript snippets for postmortems (question, tool calls, answer payload IDs).
- Expose analytics dashboard (future) summarizing top queries, ingestion failures, time-to-answer.

## Implementation Checklist
- [x] `/chat` frontend route with chat composer, attachment tray, and streaming-style progress updates.
- [ ] GPT-5 orchestration service with tool registry and guardrails.
- [ ] REST endpoints backing each tool (`offers.search_best_price`, etc.).
- [ ] Async ingestion notifier that pushes status updates to chat (WebSocket or Server-Sent Events).
- [x] Spreadsheet template stored in repo + surfaced in UI.
- [ ] Integration tests: upload → ingest → chat answer for canonical device.
