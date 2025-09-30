# Pricebot Quickstart Guide

**Get the MVP running in 5 minutes.**

---

## Prerequisites

- Python 3.11+
- Railway CLI (for deployment): `npm i -g @railway/cli`
- Git

---

## Local Setup (2 minutes)

```bash
# 1. Clone and enter directory
git clone https://github.com/itsAR-VR/pricebot
cd pricebot

# 2. Create virtual environment and install
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e '.[ocr,pdf,dev]'

# 3. Start the API server
uvicorn app.main:app --reload
```

**API now running at:** http://localhost:8000

- **Swagger docs:** http://localhost:8000/docs
- **Operator UI:** http://localhost:8000/admin/documents

---

## Ingest Your First Price Sheet (1 minute)

```bash
# Excel/CSV spreadsheet
python -m app.cli.ingest "path/to/vendor_prices.xlsx" --vendor "Vendor Name"

# WhatsApp chat export
python -m app.cli.ingest "chat.txt" --processor whatsapp_text

# Image with OCR (requires: brew install tesseract)
python -m app.cli.ingest "price_sheet.jpg" --processor document_text --vendor "Warehouse"

# List ingested documents
python -m app.cli.list_documents --limit 10
```

---

## Query the API (30 seconds)

```bash
# Get all offers
curl http://localhost:8000/offers?limit=10

# Get offers for specific vendor (replace UUID)
curl http://localhost:8000/offers?vendor_id=YOUR_VENDOR_UUID

# Get price history for a product
curl http://localhost:8000/price-history/product/YOUR_PRODUCT_UUID

# Search products
curl http://localhost:8000/products?q=iphone

# List vendors
curl http://localhost:8000/vendors
```

---

## Deploy to Railway (5 minutes)

### Step 1: Login to Railway
```bash
railway login
```
This opens your browser for authentication.

### Step 2: Create Project
```bash
railway init
# Choose: "Empty Project"
# Enter project name: "pricebot"
```

### Step 3: Add PostgreSQL
```bash
railway add postgresql
```

### Step 4: Deploy
```bash
railway up
```

Railway will:
- Build your app using `railway.json` config
- Install dependencies with OCR/PDF extras
- Set `DATABASE_URL` automatically from Postgres addon
- Expose a public URL

### Step 5: Verify Deployment
```bash
# Get your app URL
railway domain

# Check health
curl https://YOUR-APP.up.railway.app/health

# View logs
railway logs
```

### Step 6: Run Initial Migration
```bash
railway run python -c "from app.db.session import init_db; init_db()"
```

---

## Production Ingestion

Upload files to Railway persistent storage, then run CLI:

```bash
# One-time ingestion
railway run python -m app.cli.ingest storage/vendor_oct.xlsx --vendor "Vendor"

# Schedule recurring jobs in Railway dashboard
# Jobs tab → Create new job
# Command: python -m app.cli.ingest storage/daily_feed.xlsx --vendor "Supplier"
# Schedule: 0 2 * * * (daily at 2 AM)
```

---

## Environment Variables (Optional)

Set these in Railway dashboard or local `.env`:

```bash
DATABASE_URL=postgresql://user:pass@host:5432/db  # Auto-set by Railway
ENVIRONMENT=production
DEFAULT_CURRENCY=USD
INGESTION_STORAGE_DIR=/data/storage  # Use Railway volume
ENABLE_OPENAI=true
OPENAI_API_KEY=sk-...  # For LLM enrichment
```

---

## Troubleshooting

### OCR not working locally
```bash
# macOS
brew install tesseract

# Ubuntu/Debian
sudo apt-get install tesseract-ocr

# Verify
tesseract --version
```

### Database connection failed
```bash
# Check DATABASE_URL is set
echo $DATABASE_URL

# For Railway, ensure Postgres addon is linked
railway variables
```

### Import errors
```bash
# Reinstall with all extras
pip install -e '.[ocr,pdf,dev]'

# Verify installation
python -c "from app.db import models; print('OK')"
```

---

## Next Steps

- **View ingested data:** http://localhost:8000/admin/documents
- **Run tests:** `pytest`
- **Read full docs:** [docs/](.)
- **Plan features:** [PROJECT_PLAN.md](PROJECT_PLAN.md)

---

**You're ready to ingest vendor price data and serve it via API!** 🚀
