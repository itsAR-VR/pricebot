WhatsApp Web Ingest

Overview
- A Chrome extension captures WhatsApp Web messages and posts them to the Pricebot API at `/integrations/whatsapp/ingest`.
- Messages are stored in `whatsapp_chats` and `whatsapp_messages` for later deal extraction.

Server
- Env: `WHATSAPP_INGEST_TOKEN` (optional shared-secret). If set, requests must include `x-ingest-token` header.
- CORS: by default `cors_allow_all=True` for local/dev. Tighten in production.
- Route: `POST /integrations/whatsapp/ingest`
  - Body: `{ client_id: string, messages: Array<WhatsAppMessageIn> }`
  - WhatsAppMessageIn: `{ chat_title, text, observed_at?, sender_name?, sender_phone?, is_outgoing?, chat_type?, platform_id?, raw_payload? }`
  - Response: `{ accepted, created, deduped, created_chats }`

Data Model
- `whatsapp_chats`: title, chat_type, platform_id, extra
- `whatsapp_messages`: chat_id, client_id, observed_at, sender_name, sender_phone, is_outgoing, text, content_hash, raw_payload
- Dedup: content hash + recent time window (24h) per chat.

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

Notes
- DOM scraping is intentionally minimal; we’ll refine structure and parsers after validating end-to-end capture.
