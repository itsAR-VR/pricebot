# Railway Deployment Guide

This project ships with a minimal configuration for deploying the FastAPI backend on [Railway](https://railway.app). The backend exposes both JSON APIs (under `/offers`, `/products`, `/vendors`, `/price-history`, `/documents`) and the operator console (`/admin/documents`).

## 1. Create the Railway Service
1. Install the Railway CLI: `npm i -g @railway/cli` and authenticate (`railway login`).
2. From the project root run `railway init` and choose "Deploy from current repository".
3. Railway will detect the `Procfile` and `railway.json`. The service runs with
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
   ```
4. Provision a PostgreSQL database (recommended) or keep SQLite for prototypes. Set `DATABASE_URL` accordingly (e.g. `postgresql+psycopg://user:pass@host:port/db`).

## 2. Configure Environment Variables
Set the following in the Railway dashboard (or via `railway variables set`):

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | Connection string for Postgres/SQLite. |
| `APP_NAME` | Optional override for display name. |
| `ENVIRONMENT` | e.g. `production`. |
| `DEFAULT_CURRENCY` | Defaults to `USD`. |
| `INGESTION_STORAGE_DIR` | For Railway dynos use `/data/storage` (persistent disk). |
| `ENABLE_OPENAI` / `OPENAI_API_KEY` | Enable if using LLM-based enrichment. |

> Tip: mount a Railway volume and point `INGESTION_STORAGE_DIR` at the mounted path so uploaded artefacts persist between deploys.

## 3. Run the Migration Once
If using Postgres, run
```bash
railway run python -c "from app.db.session import init_db; init_db()"
```
This creates all SQLModel tables on the remote database.

## 4. Trigger Manual Ingestion Jobs
Use Railway’s one-off job runner (or `railway run`) to execute the ingestion CLI:

```bash
# Spreadsheet
railway run python -m app.cli.ingest storage/vendor_oct.xlsx --vendor "Vendor Name"

# WhatsApp transcript (stored on the Railway volume)
railway run python -m app.cli.ingest storage/whatsapp_export.txt --processor whatsapp_text

# OCR document (requires enabling the ocr extra during deploy: pip install -e .[ocr,pdf])
railway run python -m app.cli.ingest storage/invoice.png --processor document_text --vendor "Warehouse"
```

The CLI preserves each artefact under `INGESTION_STORAGE_DIR` and registers the ingestion in the `/documents` API and operator console.

## 5. Schedule Recurring Imports
Railway supports cron jobs via the "Jobs" tab. Create a job with the command you want to run, e.g.

```
python -m app.cli.ingest storage/daily_feed.xlsx --vendor "Daily Supplier"
```

and choose the schedule (`0 * * * *` for hourly). You can create separate jobs for different processors (WhatsApp transcript sync, OCR drop folder, etc.).

## 6. Operator UI Access
Once deployed, visit `https://<railway-domain>/admin/documents` to monitor artefacts, statuses, and extracted offers. The JSON API is still available at `/documents` if you prefer integrating with other dashboards.

## 7. Troubleshooting
- **Missing optional dependencies**: enable extras in the build step (e.g. set `PIP_INSTALL_EXTRA=.[ocr,pdf]`).
- **Large uploads**: configure Railway service memory and ensure `INGESTION_STORAGE_DIR` points to a persistent volume with enough space.
- **Timezones**: all timestamps are stored in UTC; the UI formats them as `YYYY-MM-DD HH:MM` (UTC).

Refer back to `railway.json` and `Procfile` for the default production command and health check path.
