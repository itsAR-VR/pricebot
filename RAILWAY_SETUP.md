# Railway Setup Workaround

**Issue:** `railway init` returns "Problem processing request"  
**Solution:** Use Railway Dashboard + GitHub integration

---

## Alternative Deployment Method (5 minutes)

### Step 1: Create Project via Dashboard

1. Go to **[railway.app](https://railway.app)**
2. Click **"New Project"**
3. Select **"Deploy from GitHub repo"**
4. Authorize GitHub if prompted
5. Select repository: **itsAR-VR/pricebot**
6. Railway will automatically:
   - Detect it's a Python app
   - Read `railway.json` for build config
   - Use `Procfile` for start command

### Step 2: Add PostgreSQL

1. In your new project dashboard
2. Click **"+ New"** → **"Database"** → **"Add PostgreSQL"**
3. Railway automatically sets `DATABASE_URL` environment variable

### Step 3: Configure Environment (Optional)

Click **"Variables"** tab and add:

```
ENVIRONMENT=production
DEFAULT_CURRENCY=USD
INGESTION_STORAGE_DIR=/data/storage
```

### Step 4: Add Volume (Optional)

1. Click **"+ New"** → **"Volume"**
2. Name: `storage`
3. Mount path: `/data/storage`

### Step 5: Deploy

Railway auto-deploys on GitHub push! Or click **"Deploy"** manually.

### Step 6: Initialize Database

Once deployed, click on your service → **"Settings"** → **"Deploy"** section:

Find your service URL, then run locally:

```bash
# Get Railway project ID from dashboard URL
# Example: railway.app/project/abc123-def456-...
# Then link locally:

cd /Users/AR180/Desktop/Codespace/pricebot
railway link

# Or set project ID manually
railway link --project <project-id>

# Initialize DB
railway run python -c "from app.db.session import init_db; init_db()"
```

### Step 7: Get Your URL

Your service URL is shown in the dashboard under **"Deployments"** tab.

Format: `https://pricebot-production-xyz.up.railway.app`

---

## Verify Deployment

```bash
# Replace with your actual URL
export RAILWAY_URL="https://your-app.up.railway.app"

# Test health
curl $RAILWAY_URL/health

# Test API
curl $RAILWAY_URL/offers?limit=5

# Open docs
open $RAILWAY_URL/docs
```

---

## Link Existing Project to CLI

If you created the project via dashboard, link it locally:

```bash
cd /Users/AR180/Desktop/Codespace/pricebot

# Option 1: Interactive linking (if TTY works)
railway link

# Option 2: Link by project ID
# Get ID from dashboard URL: railway.app/project/YOUR-PROJECT-ID
railway link --project YOUR-PROJECT-ID

# Verify
railway status
```

---

## Troubleshooting Railway CLI

### "Problem processing request"

**Possible causes:**
1. Railway API temporary issue → Retry in 5 minutes
2. Network/firewall blocking Railway API
3. CLI cache corruption

**Fixes:**

```bash
# 1. Clear Railway CLI cache
rm -rf ~/.railway

# 2. Re-login
railway logout
railway login

# 3. Update CLI to latest
npm update -g @railway/cli

# 4. Use dashboard instead (recommended for now)
```

### "The input device is not a TTY"

This happens in non-interactive shells. Solutions:

```bash
# Use dashboard for project creation
# Then link manually with project ID:
railway link --project <id>
```

### Still not working?

**Use GitHub Integration (Recommended):**

1. Create project via Railway dashboard
2. Connect to GitHub repo
3. Enable auto-deploy on push
4. Railway handles everything automatically!

**Benefits:**
- No CLI needed for deployment
- Auto-deploys on `git push`
- Easier environment management
- Better for CI/CD workflows

---

## Next Steps After Dashboard Setup

Once your project is deployed via dashboard:

1. **Check deployment status** - Should show "Success" in Deployments tab
2. **View logs** - Click on deployment → View logs
3. **Initialize database:**
   ```bash
   railway link --project <your-project-id>
   railway run python -c "from app.db.session import init_db; init_db()"
   ```
4. **Test your API** - Use the Railway-provided URL

---

## GitHub Auto-Deploy Setup

Already configured! Your `railway.json` and `Procfile` tell Railway how to build and run.

**On every `git push origin main`:**
- Railway detects the push
- Builds: `pip install -e .[ocr,pdf]`
- Starts: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Runs health check: `GET /health`
- Exposes public URL

**To trigger deployment:**
```bash
git add -A
git commit -m "deploy: initial production release"
git push origin main
```

Railway automatically deploys within 2-3 minutes!

---

## Current Deployment Status

Based on your setup:
- ✅ Railway CLI installed (v4.10.0)
- ✅ Logged in as abdursajid05@gmail.com
- ⚠️ `railway init` has API issues
- ✅ **Solution: Use dashboard + GitHub integration**

---

**Recommendation:** Use Railway Dashboard method. It's more reliable and enables auto-deploy on push, which is better for production workflows anyway!




