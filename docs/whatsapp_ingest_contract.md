# WhatsApp Ingest Contract

Defines the payload contract and operational requirements for posting WhatsApp Web events into Pricebot. This document is the source of truth for collectors and will evolve alongside backend hardening work (Tasks B–D, F in the roadmap).

---

## Endpoint

- **URL:** `POST /integrations/whatsapp/ingest`
- **Headers:**
  - `Content-Type: application/json`
  - `X-Ingest-Token: <shared secret>` — required in every environment; requests are rejected when missing.
  - `X-Signature: <hex>` — HMAC-SHA256 of the timestamp + request body. Required when `WHATSAPP_INGEST_HMAC_SECRET` is configured.
  - `X-Signature-Timestamp: <ISO8601>` — UTC timestamp used in the HMAC envelope. Must be within ±`WHATSAPP_INGEST_SIGNATURE_TTL_SECONDS` (default 300 s).
- **Body:** `WhatsAppIngestBatch` JSON payload (see schema below)
- **Response:** 200 with aggregate counters and per-message dedupe decisions (see [Response Contract](#response-contract))

> ⚠️ Set `WHATSAPP_INGEST_TOKEN` in every environment. Requests missing or mismatching `X-Ingest-Token` are rejected (401); in production a missing token configuration responds with 503 to surface misconfiguration.

---

## Request Schema

### WhatsAppIngestBatch

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `client_id` | string (3–200 chars) | ✅ | Stable identifier for the collector instance sending the batch (e.g. deployment, bot, or session ID). |
| `messages` | array\<[WhatsAppMessageIn](#whatsappmessagein)\> | ✅ | Ordered list of newly observed WhatsApp messages since the previous flush. Max 500 per request. |

JSON Schema: [`docs/schemas/whatsapp_ingest_batch.schema.json`](./schemas/whatsapp_ingest_batch.schema.json)

### WhatsAppMessageIn

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `chat_title` | string (1–200 chars) | ✅ | Display name of the chat in WhatsApp; used for routing and vendor heuristics. |
| `chat_type` | `"group" \| "direct" \| "unknown"` | ➖ | Collector classification; informs vendor mapping and extraction policies. |
| `platform_id` | string | ➖ | WhatsApp JID for the chat (`1203…@g.us`, `4477…@s.whatsapp.net`). |
| `message_id` | string | ➖ | Stable message identifier emitted by WhatsApp. Enables strict dedupe on `(chat_id, message_id)`. |
| `observed_at` | RFC 3339 timestamp | ➖ | UTC timestamp when the collector observed the message. Backend falls back to ingestion time when missing. |
| `sender_name` | string | ➖ | WhatsApp display name captured at send time. |
| `sender_phone` | string | ➖ | Sender phone number (E.164 preferred). Stored only inside `raw_payload`; surfaced masked in UI. |
| `is_outgoing` | boolean | ➖ | `true` when the logged-in account sent the message. |
| `text` | string (1–5000 chars) | ✅ | Message body. Include media placeholders such as `[image: filename.jpg]` when applicable. |
| `raw_payload` | object | ➖ | Raw Baileys event (redacted if necessary). Persisted for debugging and data lineage. |

JSON Schema: [`docs/schemas/whatsapp_message_in.schema.json`](./schemas/whatsapp_message_in.schema.json)

> Recommendation: normalize `observed_at` to UTC before sending and include raw media metadata even when bytes upload is deferred (Phase 3).

---

## Dedupe & Idempotency

1. **Primary key:** `(chat_id, message_id)` when `message_id` is provided.
2. **Fallback:** `(chat_id, content_hash)` where `content_hash = sha256(chat_title + sender_name + text)` scoped to a rolling 24 h window.
3. **Batch-level guard:** Collectors should avoid resending identical batches; when replays occur the backend reports `status=deduped`.

The service returns per-message decisions so operator tooling can surface why a message was skipped. The content-hash window is tunable via `WHATSAPP_CONTENT_HASH_WINDOW_HOURS` (default 24 h).

---

## Response Contract

```jsonc
{
  "request_id": "08c3f651-4246-4db4-84ff-6a7afac4fb54",
  "accepted": 2,
  "created": 1,
  "deduped": 1,
  "created_chats": 1,
  "decisions": [
    {
      "chat_title": "Dubai Electronics Group Deals",
      "platform_id": "120363179753111@g.us",
      "message_id": "3EB03B5F1EBD1AA3E4CF",
      "content_hash": "4cf95f2c…",
      "status": "created",
      "whatsapp_message_id": "0f0b0451-92a7-4bbf-95bb-936b2f4ec92b"
    },
    {
      "chat_title": "Vendor Support",
      "platform_id": "447700900123@s.whatsapp.net",
      "message_id": null,
      "content_hash": "1f2ad340…",
      "status": "deduped",
      "reason": "duplicate_content_hash_within_window"
    }
  ]
}
```

- `status` values: `created`, `deduped`, `skipped`.
- `reason` codes (non-exhaustive):
  - `duplicate_message_id`
  - `duplicate_content_hash_within_window`
  - `empty_text`
  - `filtered_event_type`
- For empty batches (`messages=[]`) the API responds with HTTP `422`.

> `request_id` is emitted for log correlation. Aggregated counters (`accepted`, `created`, `deduped`, `created_chats`) remain backward-compatible with the initial implementation.

---

## HMAC Signature Envelope

When `WHATSAPP_INGEST_HMAC_SECRET` is configured the collector must sign every request:

1. Serialize the JSON payload exactly as sent (`json.dumps(payload, separators=(",", ":"), ensure_ascii=False)` in Node/Python).
2. Produce an RFC 3339 UTC timestamp (include `Z` suffix) and set the `X-Signature-Timestamp` header.
3. Concatenate `timestamp + "." + body` (timestamp as UTF-8, body as bytes) and compute `hex(hmac_sha256(secret, message))`.
4. Send the resulting lowercase hex digest in `X-Signature`.

The server will reject signatures older than `WHATSAPP_INGEST_SIGNATURE_TTL_SECONDS` (default 300 s) or mismatched digests with HTTP 403. Missing signature headers while the secret is configured return HTTP 401.

---

## Error Codes

| HTTP | Scenario | Notes |
| --- | --- | --- |
| `400` | Malformed JSON | Prior to validation. |
| `401` | Missing/invalid `X-Ingest-Token` | Enforced whenever `WHATSAPP_INGEST_TOKEN` is set. |
| `403` | Failed HMAC (`X-Signature`) | Added in Task B; request should be retried with fresh timestamp and body. |
| `422` | Schema validation error | Payload does not conform to the JSON Schema. |
| `429` | Rate-limited | Token bucket per `client_id`; clients should honor `Retry-After`. |
| `503` | Token not configured in production | Protection against unintentionally exposed endpoints. |
| `5xx` | Unexpected server error | Logged with request ID for follow-up. |

---

## Sample Batch

```json
{
  "client_id": "collector-primary",
  "messages": [
    {
      "chat_title": "Dubai Electronics Group Deals",
      "chat_type": "group",
      "platform_id": "120363179753111@g.us",
      "message_id": "3EB03B5F1EBD1AA3E4CF",
      "observed_at": "2025-02-18T12:15:02Z",
      "sender_name": "Ahmed Khan",
      "sender_phone": "+971501234567",
      "is_outgoing": false,
      "text": "iPhone 15 256GB - AED 3610, stock 20 units, delivery today.",
      "raw_payload": {
        "messageTimestamp": 1739880902,
        "type": "conversation"
      }
    },
    {
      "chat_title": "Vendor Support",
      "chat_type": "direct",
      "platform_id": "447700900123@s.whatsapp.net",
      "observed_at": "2025-02-18T12:15:07Z",
      "sender_name": "Pricebot Ops",
      "sender_phone": "+971509999999",
      "is_outgoing": true,
      "text": "Got it, logging the offer."
    }
  ]
}
```

Validate the payload locally:

```bash
python - <<'PY'
import json
from pathlib import Path
from jsonschema import Draft202012Validator, RefResolver

batch_path = Path("docs/schemas/whatsapp_ingest_batch.schema.json").resolve()
message_path = batch_path.parent / "whatsapp_message_in.schema.json"

payload = json.loads(Path("docs/whatsapp_ingest_contract_sample.json").read_text())

batch_schema = json.loads(batch_path.read_text())
message_schema = json.loads(message_path.read_text())

store = {
    batch_schema.get("$id", str(batch_path)): batch_schema,
    message_schema.get("$id", str(message_path)): message_schema,
}
resolver = RefResolver(base_uri=batch_path.parent.as_uri() + "/", referrer=batch_schema, store=store)
Draft202012Validator(batch_schema, resolver=resolver).validate(payload)
print("payload is valid")
PY
```

> The repository includes the same JSON sample at `docs/whatsapp_ingest_contract_sample.json` for regression tests and future integration test fixtures.

---

## Operational Guidelines

- **Batching:** Flush every 1–2 s or when 50 messages accumulate, whichever comes first. Respect WhatsApp Web rate limits by backing off on repeated failures.
- **Clock drift:** Keep collector clocks within ±30 s of UTC to ensure dedupe windows behave predictably.
- **Retries:** Use exponential backoff (starting at 1 s, max 30 s). On `401`/`403` prompt operator to refresh credentials; on `429` obey `Retry-After`.
- **Logging:** Include `client_id`, `batch_size`, and ingest latency in structured logs (`sent`, `accepted`, `created`, `deduped` counters).
- **Security:** Rotate `WHATSAPP_INGEST_TOKEN` quarterly. HMAC shared secret should differ from the ingest token once Task B lands.

---

## Related Documents

- [WhatsApp Integration Overview](./integrations_whatsapp.md)
- [Ingestion Playbook](./ingestion_playbook.md)
- [WhatsApp Collector Roadmap](../AGENTS.md)
