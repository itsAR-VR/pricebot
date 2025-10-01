# Railway Deployment Instructions

**Deploy Pricebot to production in 5 minutes.**

---

## Prerequisites

- Railway CLI installed: `npm i -g @railway/cli`
- Railway account (free tier available)
- Git repository pushed to GitHub

---

## Step-by-Step Deployment

### 1. Login to Railway

```bash
railway login
```

This opens your browser for GitHub authentication. Once logged in, return to terminal.

### 2. Initialize Project

```bash
cd /path/to/pricebot
railway init
```

**Prompts:**
- "Would you like to create a new project?" → **Yes**
- "Project name" → **pricebot** (or your choice)
- "Environment" → **production** (default)

Railway creates a new project and links your local directory.

### 3. Add PostgreSQL Database

```bash
railway add postgresql
```

Railway will:
- Provision a Postgres database
- Set `DATABASE_URL` environment variable automatically
- Link the database to your project

### 4. Configure Environment Variables (Optional)

Set additional variables via dashboard or CLI:

```bash
# Via CLI
railway variables set ENVIRONMENT=production
railway variables set DEFAULT_CURRENCY=USD
railway variables set INGESTION_STORAGE_DIR=/data/storage

# Or via Railway dashboard:
# 1. Visit railway.app
# 2. Select your project → Variables tab
# 3. Add variables
```

**Recommended Variables:**

| Variable | Value | Required |
|----------|-------|----------|
| `DATABASE_URL` | Auto-set by Postgres addon | ✅ Yes |
| `ENVIRONMENT` | `production` | No |
| `DEFAULT_CURRENCY` | `USD` | No |
| `INGESTION_STORAGE_DIR` | `/data/storage` | No |
| `ENABLE_OPENAI` | `false` | No |
| `OPENAI_API_KEY` | `sk-...` | Only if ENABLE_OPENAI=true |

### 5. Add Persistent Storage (Optional but Recommended)

```bash
railway volume create
```

**Prompts:**
- "Volume name" → **storage**
- "Mount path" → **/data/storage**

This ensures uploaded files persist across deployments.

### 6. Deploy Application

```bash
railway up
```

Railway will:
1. Build your app using `railway.json` config
2. Install dependencies: `pip install -e .[ocr,pdf]`
3. Run health checks on `/health` endpoint
4. Expose public URL

**Build command (from railway.json):**
```bash
pip install -e .[ocr,pdf]
```

**Start command (from railway.json):**
```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

### 7. Initialize Database Schema

After first deployment, run:

```bash
railway run python -c "from app.db.session import init_db; init_db()"
```

This creates all database tables.

### 8. Get Your App URL

```bash
railway domain
```

Output will show your public URL:
```
https://pricebot-production-xyz.up.railway.app
```

### 9. Verify Deployment

```bash
# Check health
curl https://your-app.up.railway.app/health

# View API docs
open https://your-app.up.railway.app/docs

# Check operator UI
open https://your-app.up.railway.app/admin/documents
```

---

## Post-Deployment Tasks

### Upload Initial Data

```bash
# Upload a file to Railway volume
railway run python -m app.cli.ingest storage/vendor_prices.xlsx --vendor "Vendor Name"
```

### Schedule Recurring Ingestion Jobs

1. Go to Railway dashboard → your project
2. Click "Jobs" tab
3. Create new job:
   - **Name:** Daily Price Update
   - **Command:** `python -m app.cli.ingest storage/daily_feed.xlsx --vendor "Supplier"`
   - **Schedule:** `0 2 * * *` (daily at 2 AM)
4. Save job

### Monitor Logs

```bash
# Real-time logs
railway logs

# Or view in dashboard:
# Railway.app → your project → Deployments → View logs
```

### Set Up Custom Domain (Optional)

1. Railway dashboard → Settings → Domains
2. Click "Add Domain"
3. Enter your domain (e.g., `api.pricebot.com`)
4. Add CNAME record to your DNS:
   - **Name:** `api`
   - **Value:** `your-app.up.railway.app`
5. Wait for DNS propagation (5-30 minutes)

---

## Updating the Application

### Deploy New Changes

```bash
git add -A
git commit -m "feat: new feature"
git push origin main

# Deploy to Railway
railway up
```

Railway automatically redeploys on push if GitHub integration is enabled.

### Rollback to Previous Version

```bash
# Via CLI
railway rollback

# Or via dashboard:
# Deployments tab → Select previous deployment → Rollback
```

---

## Environment-Specific Deployments

### Staging Environment

```bash
# Create staging environment
railway environment create staging

# Deploy to staging
railway up --environment staging
```

### Production vs Staging

| Feature | Production | Staging |
|---------|-----------|---------|
| Database | Postgres (paid tier) | Postgres (free tier) |
| Storage | 10GB volume | 1GB volume |
| Domain | Custom domain | Railway subdomain |
| Variables | Production keys | Test keys |

---

## Troubleshooting

- **Python runtime < 3.11** – Pricebot targets Python 3.11+. If your deployment image defaults to 3.9, pin 3.11 by exporting `PYTHON_VERSION=3.11` in the build command (see `railway.json`) or base your Dockerfile on a 3.11 image before deploying.
- **OCR uploads returning 500** – Install the optional extras (`pip install -e '.[ocr,pdf]'`) and include the OS dependency `tesseract-ocr` (Nixpacks package or custom Docker layer) so image/PDF processing works.
- **Uploads failing after apparent success** – Verify the `/data/storage` volume is mounted and that `INGESTION_STORAGE_DIR` points to it; without a writable volume the API cannot persist source documents.

### Build Fails

**Error:** `No module named 'app'`

**Fix:** Ensure `pyproject.toml` has correct package config:
```toml
[tool.setuptools.packages.find]
where = ["app"]
include = ["*"]
```

**Error:** `pytesseract.TesseractNotFoundError`

**Fix:** Tesseract is installed via buildpack. Ensure `railway.json` includes OCR extras:
```json
{
  "build": {
    "buildCommand": "pip install -e .[ocr,pdf]"
  }
}
```

### Health Check Fails

**Error:** `Health check timeout`

**Fix:** Verify `/health` endpoint returns 200:
```bash
railway run curl http://localhost:8000/health
```

Check `railway.json` config:
```json
{
  "deploy": {
    "healthcheckPath": "/health",
    "healthcheckTimeout": 60
  }
}
```

### Database Connection Error

**Error:** `sqlalchemy.exc.OperationalError: could not connect`

**Fix:** 
1. Verify DATABASE_URL is set: `railway variables`
2. Check Postgres addon is running: Railway dashboard → Resources
3. Re-link addon: `railway add postgresql`

### File Storage Not Persisting

**Fix:** Ensure volume is mounted:
```bash
railway volume list

# If missing, create and mount
railway volume create
# Mount path: /data/storage
```

Update `INGESTION_STORAGE_DIR` variable:
```bash
railway variables set INGESTION_STORAGE_DIR=/data/storage
```

---

## Cost Estimates

**Railway Pricing (as of Sep 2025):**

| Resource | Free Tier | Paid Tier |
|----------|-----------|-----------|
| Compute | $5/month | $5-20/month |
| Postgres | $5/month (500MB) | $10-50/month |
| Storage | 1GB free | $0.25/GB/month |
| **Total** | ~$10/month | ~$20-75/month |

**Cost Optimization:**
- Use SQLite for staging (no Postgres cost)
- Start with free tier, upgrade as needed
- Monitor usage in Railway dashboard

---

## Security Best Practices

### 1. Protect Operator UI

Add basic auth middleware (planned for Phase 2):

```python
# app/ui/views.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.username != "admin" or credentials.password != "secret":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
```

### 2. Use Environment Variables

Never commit secrets to git:
```bash
# ❌ Don't do this
DATABASE_URL=postgresql://user:pass@host/db

# ✅ Do this
railway variables set DATABASE_URL="postgresql://..."
```

### 3. Enable HTTPS

Railway provides free SSL certificates automatically.

### 4. Restrict Database Access

Railway Postgres is private by default (not exposed to internet).

---

## Monitoring & Alerts

### Railway Built-in Monitoring

1. Dashboard → Metrics tab
2. View CPU, memory, request count
3. Set up alerts for downtime

### External Monitoring (Optional)

Use UptimeRobot or Pingdom:
1. Add health check monitor
2. URL: `https://your-app.up.railway.app/health`
3. Interval: 5 minutes
4. Alert via email/SMS on failure

---

## Backup & Recovery

### Database Backups

Railway auto-backs up Postgres daily (retention: 7 days on paid tier).

**Manual backup:**
```bash
railway run pg_dump > backup.sql
```

### Restore from Backup

```bash
railway run psql < backup.sql
```

### Volume Snapshots

Create snapshot before major changes:
1. Railway dashboard → Volumes
2. Select volume → Create snapshot
3. Restore if needed

---

## Next Steps

✅ **Deployment Complete!**

Now you can:
- Upload vendor price sheets via CLI
- Query API at your Railway URL
- Monitor ingestion via operator UI
- Schedule automated jobs in Railway dashboard

**Resources:**
- [Railway Docs](https://docs.railway.app)
- [Pricebot API Reference](docs/API_REFERENCE.md)
- [Project Plan](docs/PROJECT_PLAN.md)

---

**Questions?** Open an issue on [GitHub](https://github.com/itsAR-VR/pricebot/issues)





