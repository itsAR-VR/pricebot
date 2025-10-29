# WhatsApp Collector

Node.js/TypeScript sidecar that uses [Baileys](https://github.com/adiwajshing/Baileys) to stream live WhatsApp messages into the Pricebot ingest pipeline.

The service listens to WhatsApp Web events, normalises them into the `WhatsAppMessageIn` contract, and forwards messages in small batches to the backend `/integrations/whatsapp/ingest` endpoint.

## Features

- QR-based session bootstrap with persistent auth state for headless redeploys.
- Automatic reconnect on network drops or WhatsApp Web timeouts.
- Message batching with configurable cadence and size thresholds.
- Filtering for reactions, join/leave notices, status updates, and other non-actionable events.
- Structured JSON logs, ingest counters, `/healthz` readiness, and `/metrics` JSON snapshot.
- Container ready for Railway deployments.

## Requirements

- Node.js 20+
- npm 9+ (or compatible package manager)
- A WhatsApp account that can be linked through WhatsApp Web
- Backend ingest endpoint with a valid `WHATSAPP_INGEST_TOKEN` configured

## Setup

```bash
cd whatsapp-collector
npm install
cp .env.example .env
# edit the .env file with real values
```

## Local development

```bash
npm run dev
```

The first run prints a QR code. Scan it from the WhatsApp mobile app (`Linked devices` > `Link a device`). Auth state files are stored in `AUTH_STATE_DIR` (default `./auth-state`); keep this directory between restarts.

## Build & run

```bash
npm run build
npm start
```

Logs are emitted in JSON. The `/healthz` endpoint returns `200` once the WhatsApp socket is connected. `/metrics` exposes the current ingest counters.

### Health & metrics

```bash
curl localhost:8080/healthz
curl localhost:8080/metrics | jq
```

- `status` is `ok` when the WhatsApp socket is online.
- Counters include batches/messages sent along with `created|accepted|deduped|skipped` tallies returned by the backend.

## Environment variables

| Variable | Description |
| --- | --- |
| `WHATSAPP_INGEST_URL` | Absolute URL to the backend ingest endpoint. |
| `WHATSAPP_INGEST_TOKEN` | Shared secret sent as `X-Ingest-Token`. |
| `CLIENT_ID` | Identifier for this collector instance (e.g. environment or tenant). |
| `AUTH_STATE_DIR` | Directory to persist Baileys auth state to disk. |
| `BATCH_MAX_MESSAGES` | Max messages per batch (default `50`). |
| `BATCH_FLUSH_INTERVAL_MS` | Flush cadence in milliseconds (default `1500`). |
| `LOG_LEVEL` | `trace|debug|info|warn|error` (default `info`). |
| `PORT` | Port for health and metrics server (default `8080`). |

## Directory layout

- `src/index.ts` – service bootstrap, wiring, and graceful shutdown handling.
- `src/config.ts` – environment configuration loader.
- `src/logger.ts` – JSON logger instance.
- `src/collector` – WhatsApp socket lifecycle (`messages.upsert`, reconnects).
- `src/normalizer.ts` – maps Baileys payloads into the ingest contract.
- `src/batcher.ts` – buffered batching with interval and size thresholds.
- `src/ingest-client.ts` – HTTP client with retries and token auth.
- `src/metrics.ts` – in-memory counters exposed via `/metrics`.
- `src/server.ts` – health/metrics HTTP server.

## Docker

Build and run locally:

```bash
docker build -t whatsapp-collector .
docker run --rm \
  -p 8080:8080 \
  -e WHATSAPP_INGEST_URL=https://api.example.com/integrations/whatsapp/ingest \
  -e WHATSAPP_INGEST_TOKEN=secret \
  -e CLIENT_ID=local-dev \
  -v $(pwd)/auth-state:/app/auth-state \
  whatsapp-collector
```

Mount a persistent volume to `/app/auth-state` so the QR scan is preserved across restarts.

## Deployment notes

The provided `Dockerfile` produces a lean Node 20 image. Mount a persistent volume to `AUTH_STATE_DIR` (e.g. Railway persistent storage) to avoid rescanning QR codes on redeploys.

Rotate `WHATSAPP_INGEST_TOKEN` regularly and treat it like a password. WhatsApp Web automation can violate WhatsApp ToS; review compliance requirements before production rollout. Keep auth-state backups secure—anyone with those files can impersonate the WhatsApp session.
