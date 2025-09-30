# 🎉 Post-Deployment Checklist

**Congratulations!** Your Pricebot is now live on Railway.

---

## ✅ **Immediate Setup (Required)**

### **1. Get Your App URL**

From your Railway dashboard, find your service URL:
```
https://web-production-cd557.up.railway.app
```

Save this as an environment variable:
```bash
export RAILWAY_URL="https://web-production-cd557.up.railway.app"
```

### **2. Verify Deployment**

```bash
# Test health endpoint
curl $RAILWAY_URL/health

# Expected response:
# {
#   "status": "ok",
#   "service": "Pricebot",
#   "environment": "production",
#   "version": "0.1.0"
# }
```

### **3. Initialize Database Schema**

You have two options:

#### **Option A: Railway Dashboard (Easiest)**
1. Go to [Railway Dashboard](https://railway.app) → Your Project
2. Click on your **web service**
3. Click **"Settings"** tab → Scroll to **"Deploy"** section
4. Under **"Custom Start Command"**, temporarily change to:
   ```bash
   python -c "from app.db.session import init_db; init_db()" && uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```
5. Click **"Redeploy"**
6. After deployment, change back to original:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```

#### **Option B: Railway CLI**
```bash
# 1. Get project ID from dashboard URL: railway.app/project/YOUR-PROJECT-ID
# 2. Link your local directory
railway link --project YOUR-PROJECT-ID

# 3. Initialize database
railway run python -c "from app.db.session import init_db; init_db()"
```

### **4. Test API Endpoints**

```bash
# Open interactive API docs
open $RAILWAY_URL/docs

# Test offers endpoint
curl "$RAILWAY_URL/offers?limit=5" | jq

# Test vendors endpoint
curl "$RAILWAY_URL/vendors" | jq

# Test products endpoint
curl "$RAILWAY_URL/products?limit=10" | jq
```

---

## 📊 **Data Ingestion**

### **Ingest Your First Dataset**

#### **Via Local CLI (Upload to Railway)**

```bash
cd /Users/AR180/Desktop/Codespace/pricebot

# Link to Railway first
railway link --project YOUR-PROJECT-ID

# Upload a spreadsheet
railway run python -m app.cli.ingest \
  "../Raw Data from Abdursajid.xlsx" \
  --vendor "Abdursajid"

# Check ingestion results
railway run python -m app.cli.list_documents --limit 10
```

#### **Via API (POST Files)**

```bash
# Upload via multipart/form-data
curl -X POST "$RAILWAY_URL/documents/upload" \
  -F "file=@../Raw Data from Abdursajid.xlsx" \
  -F "vendor_name=Abdursajid" \
  -F "processor=spreadsheet" \
  | jq
```

### **Verify Data Was Ingested**

```bash
# Check documents
curl "$RAILWAY_URL/documents" | jq

# Check offers
curl "$RAILWAY_URL/offers?limit=20" | jq

# Check vendors
curl "$RAILWAY_URL/vendors" | jq
```

---

## 🔧 **Environment Configuration**

### **Review Current Variables**

```bash
railway variables
```

### **Recommended Production Variables**

Set these in Railway Dashboard → Variables:

```bash
# Required
DATABASE_URL=<auto-set-by-railway>
PORT=<auto-set-by-railway>

# Optional but Recommended
ENVIRONMENT=production
DEFAULT_CURRENCY=USD
INGESTION_STORAGE_DIR=/data/storage

# If using OpenAI features
ENABLE_OPENAI=false
# OPENAI_API_KEY=sk-... (only if ENABLE_OPENAI=true)
```

---

## 📈 **Monitoring & Maintenance**

### **1. Set Up Persistent Storage**

```bash
# Create volume for file uploads
railway volume create

# When prompted:
# - Name: storage
# - Mount path: /data/storage
```

Update environment variable:
```bash
railway variables set INGESTION_STORAGE_DIR=/data/storage
```

### **2. Monitor Application Health**

**Railway Dashboard:**
- Go to your project → **Observability** tab
- Monitor CPU, Memory, and Request metrics
- Set up alerts for downtime

**External Monitoring (Optional):**
```bash
# Use a service like UptimeRobot or Pingdom
# Monitor: https://web-production-cd557.up.railway.app/health
# Frequency: Every 5 minutes
```

### **3. View Logs**

```bash
# Real-time logs via CLI
railway logs

# Or view in dashboard:
# Railway.app → Your Project → Logs tab
```

### **4. Database Backups**

Railway automatically backs up PostgreSQL daily (7-day retention on paid tier).

**Manual backup:**
```bash
railway run pg_dump > backup_$(date +%Y%m%d).sql
```

---

## 🚀 **Production Workflows**

### **Daily Operations**

#### **Ingest New Price Sheets**
```bash
# Upload new vendor data
railway run python -m app.cli.ingest \
  "new_vendor_prices.xlsx" \
  --vendor "Vendor Name"

# Verify ingestion
railway run python -m app.cli.list_documents --limit 5
```

#### **Query Latest Prices**
```bash
# Get recent offers
curl "$RAILWAY_URL/offers?limit=50&sort=desc" | jq

# Get product price history
curl "$RAILWAY_URL/price-history/product/PRODUCT_ID" | jq

# Get vendor offerings
curl "$RAILWAY_URL/price-history/vendor/VENDOR_ID" | jq
```

### **Automated Ingestion (Scheduled Jobs)**

Set up recurring jobs in Railway:

1. **Dashboard** → Your Project → **Jobs** tab
2. Click **"New Job"**
3. Configure:
   - **Name:** Daily Price Update
   - **Command:** `python -m app.cli.ingest /data/storage/daily_feed.xlsx --vendor "Daily Feed"`
   - **Schedule:** `0 2 * * *` (daily at 2 AM UTC)

---

## 🔐 **Security Best Practices**

### **1. Protect Operator UI**

Currently, the operator dashboard at `/admin/documents` is **publicly accessible**.

**Phase 2 Enhancement (Planned):**
- Add HTTP Basic Auth
- Require authentication for `/admin/*` routes
- See `DEPLOY.md` Security Best Practices section

### **2. Secure Environment Variables**

```bash
# ✅ DO: Store in Railway Variables
railway variables set DATABASE_URL="postgresql://..."

# ❌ DON'T: Commit to git or hardcode
```

### **3. Monitor for Suspicious Activity**

```bash
# Check recent API usage
railway logs | grep "POST /offers"
railway logs | grep "POST /documents/upload"
```

---

## 📊 **Performance Optimization**

### **1. Database Indexing**

Your models already have indexes on:
- `Product.model_number`
- `Vendor.name`
- `Offer.product_id`, `Offer.vendor_id`

Monitor query performance:
```bash
railway run psql -c "SELECT * FROM pg_stat_activity WHERE state = 'active';"
```

### **2. Monitor Resource Usage**

Railway Dashboard → **Metrics**:
- CPU usage should be < 50% under normal load
- Memory usage should be < 80%
- If either spikes, consider upgrading

### **3. Caching (Future Enhancement)**

Consider adding Redis caching for:
- Frequently queried products
- Vendor lists
- Price history lookups

---

## 🔄 **Deploying Updates**

### **Standard Workflow**

```bash
cd /Users/AR180/Desktop/Codespace/pricebot

# 1. Make your changes
# 2. Test locally
source .venv/bin/activate
uvicorn app.main:app --reload

# 3. Commit and push
git add -A
git commit -m "feat: add new feature"
git push origin main

# 4. Railway auto-deploys!
# Monitor in dashboard: Deployments tab
```

### **Rollback if Needed**

```bash
# Via CLI
railway rollback

# Or via dashboard:
# Deployments tab → Select previous deployment → Rollback
```

---

## 📞 **Troubleshooting**

### **Health Check Fails**
```bash
# Check logs for errors
railway logs

# Verify database connection
railway variables | grep DATABASE_URL

# Test database connectivity
railway run python -c "from app.db.session import get_engine; get_engine(); print('✅ DB connected')"
```

### **No Data Returned from API**
```bash
# Check if database is initialized
railway run psql -c "SELECT COUNT(*) FROM vendors;"

# Check if data was ingested
railway run python -m app.cli.list_documents
```

### **File Uploads Failing**
```bash
# Verify storage volume is mounted
railway volumes

# Check INGESTION_STORAGE_DIR variable
railway variables | grep STORAGE
```

---

## 🎯 **Next Phase Features**

Based on your roadmap (`docs/PROJECT_PLAN.md`), here's what's coming:

### **Phase 2 (Weeks 2-3)**
- [ ] WhatsApp chat dump parsing
- [ ] OCR for image-based price sheets
- [ ] Operator UI authentication
- [ ] Manual price overrides

### **Phase 3 (Weeks 4-5)**
- [ ] LLM-assisted extraction
- [ ] Advanced deduplication
- [ ] Offer approval workflow
- [ ] Automated quality scoring

### **Phase 4 (Week 6)**
- [ ] Multi-tenant support
- [ ] Advanced analytics
- [ ] Export to Excel/CSV
- [ ] Webhook notifications

---

## 📚 **Useful Resources**

| Resource | Link |
|----------|------|
| **API Documentation** | `$RAILWAY_URL/docs` |
| **Railway Dashboard** | https://railway.app |
| **Project Plan** | `docs/PROJECT_PLAN.md` |
| **Data Schema** | `docs/data_schema.md` |
| **Deployment Guide** | `DEPLOY.md` |
| **Health Check Fix** | `HEALTH_CHECK_FIX.md` |

---

## ✅ **Quick Verification Checklist**

Copy and paste this into your terminal to verify everything works:

```bash
# Set your Railway URL
export RAILWAY_URL="https://web-production-cd557.up.railway.app"

# Run verification tests
echo "🔍 Testing Health Endpoint..."
curl -s "$RAILWAY_URL/health" | jq && echo "✅ Health check passed" || echo "❌ Health check failed"

echo ""
echo "🔍 Testing API Documentation..."
curl -s "$RAILWAY_URL/docs" > /dev/null && echo "✅ API docs accessible" || echo "❌ API docs failed"

echo ""
echo "🔍 Testing Vendors Endpoint..."
curl -s "$RAILWAY_URL/vendors" | jq && echo "✅ Vendors endpoint working" || echo "❌ Vendors endpoint failed"

echo ""
echo "🔍 Testing Offers Endpoint..."
curl -s "$RAILWAY_URL/offers?limit=5" | jq && echo "✅ Offers endpoint working" || echo "❌ Offers endpoint failed"

echo ""
echo "🎉 All tests complete!"
```

---

## 🚀 **You're Live!**

**Deployment Status:** ✅ **Production**  
**API URL:** `https://web-production-cd557.up.railway.app`  
**Database:** ✅ **PostgreSQL (Railway)**  
**Auto-Deploy:** ✅ **Enabled on git push**

**What to do now:**
1. ✅ Initialize database (see step 3 above)
2. ✅ Ingest your first dataset
3. ✅ Test all API endpoints
4. ✅ Share API docs URL with your team
5. ✅ Set up monitoring and alerts

---

**Need help?** Check the troubleshooting section or open an issue on [GitHub](https://github.com/itsAR-VR/pricebot/issues).
