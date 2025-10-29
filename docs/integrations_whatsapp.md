WhatsApp Web Ingest

Overview
- A Chrome extension captures WhatsApp Web messages and posts them to the Pricebot API at `/integrations/whatsapp/ingest`.
- Messages are stored in `whatsapp_chats` and `whatsapp_messages` for later deal extraction.

Server
- Env: `WHATSAPP_INGEST_TOKEN` (required shared secret), `WHATSAPP_INGEST_HMAC_SECRET` (optional second secret for request signing), `WHATSAPP_CONTENT_HASH_WINDOW_HOURS` (24 default), `WHATSAPP_INGEST_RATE_LIMIT_PER_MINUTE` + `_BURST` (token bucket), `WHATSAPP_EXTRACT_DEBOUNCE_SECONDS` (auto-extract delay).
- CORS: by default `cors_allow_all=True` for local/dev. Tighten in production.
- Route: `POST /integrations/whatsapp/ingest`
  - Body: `{ client_id: string, messages: Array<WhatsAppMessageIn> }`
  - WhatsAppMessageIn: `{ chat_title, text, observed_at?, sender_name?, sender_phone?, is_outgoing?, chat_type?, platform_id?, raw_payload? }`
  - Headers: `X-Ingest-Token`, `X-Signature`, `X-Signature-Timestamp` (when HMAC enabled)
  - Response: `{ request_id, accepted, created, deduped, created_chats, decisions: [...] }`

Data Model
- `whatsapp_chats`: title, chat_type, platform_id, extra
- `whatsapp_messages`: chat_id, client_id, observed_at, sender_name, sender_phone, is_outgoing, text, content_hash, raw_payload
- Dedup: strict `(chat_id, message_id)` plus content hash within configurable window (`WHATSAPP_CONTENT_HASH_WINDOW_HOURS`).

Chrome Extension
- Folder: `whatsapp-extension/`
- Options: endpoint URL, ingest token, enable toggle. Generates a client ID on first run.
- Background batches events every ~10s and retries on failure.

Local Dev
- Backend: `uvicorn app.main:app --reload`
- Extension: load unpacked from `whatsapp-extension/` in Chrome `chrome://extensions`.
- Visit `web.whatsapp.com` and open a chat; messages will stream to the backend.

Operator UI
- Chats: `/admin/whatsapp` shows captured chats with counts and last message time.
- Chat Detail: `/admin/whatsapp/{chat_id}` lists recent messages and lets you trigger extraction.
- Diagnostics: `/chat/tools/diagnostics` exposes live counters (`accepted`, `created`, `deduped`, `extracted`, `errors`) per `client_id`/chat combo.
- Vendor mapping: the chat detail page now includes a "Map to vendor" selector that persists `vendor_id` on `whatsapp_chats` and ensures extractions emit offers under the correct supplier.

Notes
- DOM scraping is intentionally minimal; we’ll refine structure and parsers after validating end-to-end capture.
- Auto-extraction: successful ingests schedule `POST /integrations/whatsapp/chats/{chat_id}/extract-latest` after a small debounce window. Replays that dedupe do not trigger extraction.
- Rate limiting: back off when the API returns `429` and respect the `Retry-After` header.
- HMAC: collectors must sign requests with `X-Signature` when `WHATSAPP_INGEST_HMAC_SECRET` is set. See `docs/whatsapp_ingest_contract.md`.

Security & Hardening
- **Canonical signature string:** compute `HMAC_SHA256(secret, timestamp + "." + body)` and send the lowercase hex digest in `X-Signature`. Timestamps must be ISO-8601 UTC; the backend rejects signatures older than `WHATSAPP_INGEST_SIGNATURE_TTL_SECONDS` (300 s by default).
- **Clock drift:** keep collector hosts synced with NTP so signatures do not fall outside the TTL window. Increasing the TTL buys time during rotations but weakens replay protection.
- **Token rotation:** rotate `WHATSAPP_INGEST_TOKEN` and HMAC secrets as documented in `docs/ingestion_playbook.md#72-token--hmac-rotation`. Always redeploy the backend before distributing new tokens to collectors.
- **Network allowlists:** place the backend behind an allowlisting proxy (Railway IP filters, Cloudflare, AWS ALB security groups, etc.) so only collector egress addresses can hit `/integrations/whatsapp/*`.
- **Logging hygiene:** ingest responses embed a `request_id`; include it when filing incidents so secrets do not appear in logs or dashboards.
