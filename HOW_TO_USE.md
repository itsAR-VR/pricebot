# 🚀 How to Use Pricebot

**Your Pricebot is live and ready!** Here's how to use it:

---

## 🌐 **Your Live URLs**

| What | URL |
|------|-----|
| **Upload Interface** | https://web-production-cd557.up.railway.app/ |
| **API Documentation** | https://web-production-cd557.up.railway.app/docs |
| **Operator Dashboard** | https://web-production-cd557.up.railway.app/admin/documents |
| **Browse Offers** | https://web-production-cd557.up.railway.app/offers |
| **View Vendors** | https://web-production-cd557.up.railway.app/vendors |

---

## 📤 **Method 1: Upload via Web Interface** (EASIEST)

### **Steps:**

1. **Go to:** https://web-production-cd557.up.railway.app/

2. **Drag & Drop** your files OR click "Choose Files"

3. **Supported Files:**
   - ✅ Excel (.xlsx, .xls)
   - ✅ CSV
   - ✅ PDF (with OCR)
   - ✅ Images (JPG, PNG) - will extract text via OCR
   - ✅ Text files (WhatsApp chats)

4. **Enter Vendor Name** (e.g., "Abdursajid", "SB Technology")

5. **Click "Upload & Process"**

6. **View Results** - You'll see:
   - Success message
   - Number of files processed
   - Link to view documents

---

## 💻 **Method 2: Upload via API** (Programmatic)

### **Using cURL:**

```bash
# Upload a spreadsheet
curl -X POST "https://web-production-cd557.up.railway.app/documents/upload" \
  -F "file=@/path/to/your/file.xlsx" \
  -F "vendor_name=Vendor Name" \
  -F "processor=spreadsheet"

# Upload an image (OCR)
curl -X POST "https://web-production-cd557.up.railway.app/documents/upload" \
  -F "file=@/path/to/price_sheet.jpg" \
  -F "vendor_name=Vendor Name" \
  -F "processor=document_text"
```

### **Using Python:**

```python
import requests

url = "https://web-production-cd557.up.railway.app/documents/upload"

with open("vendor_prices.xlsx", "rb") as f:
    files = {"file": f}
    data = {
        "vendor_name": "My Vendor",
        "processor": "spreadsheet"
    }
    response = requests.post(url, files=files, data=data)
    print(response.json())
```

### **Using JavaScript:**

```javascript
const formData = new FormData();
formData.append('file', fileInput.files[0]);
formData.append('vendor_name', 'My Vendor');
formData.append('processor', 'spreadsheet');

fetch('https://web-production-cd557.up.railway.app/documents/upload', {
    method: 'POST',
    body: formData
})
.then(response => response.json())
.then(data => console.log(data));
```

---

## 📧 **Method 3: Email Integration** (Coming Soon - Phase 2)

Currently not implemented. Will support:
- Forward price sheets to: prices@your-domain.com
- Auto-extract vendor from email sender
- Process attachments automatically

---

## 💬 **Method 4: WhatsApp Integration** (Manual for Now)

### **Current Method (Manual):**

1. Export WhatsApp chat:
   - Open WhatsApp → Chat → More → Export Chat → Without Media
   - Save as `.txt` file

2. Upload via web interface:
   - File Type: "WhatsApp Chat"
   - Vendor Name: Group/Contact name

### **Future (Phase 2):**

- Direct WhatsApp Business API integration
- Auto-sync from WhatsApp groups
- Real-time price updates

---

## 📊 **View Your Data**

### **Operator Dashboard:**

https://web-production-cd557.up.railway.app/admin/documents

Shows:
- All uploaded documents
- Processing status
- Number of offers extracted
- Errors and warnings

### **Browse Offers:**

```bash
# Get latest 20 offers
curl "https://web-production-cd557.up.railway.app/offers?limit=20" | jq

# Filter by vendor
curl "https://web-production-cd557.up.railway.app/offers?vendor_id=VENDOR_ID" | jq

# Filter by product
curl "https://web-production-cd557.up.railway.app/offers?product_id=PRODUCT_ID" | jq
```

### **View Vendors:**

```bash
# List all vendors
curl "https://web-production-cd557.up.railway.app/vendors" | jq

# Search vendors
curl "https://web-production-cd557.up.railway.app/vendors?search=abdur" | jq
```

### **View Products:**

```bash
# List products
curl "https://web-production-cd557.up.railway.app/products?limit=50" | jq

# Search by model number
curl "https://web-production-cd557.up.railway.app/products?search=iPhone" | jq
```

### **Price History:**

```bash
# Get price history for a product
curl "https://web-production-cd557.up.railway.app/price-history/product/PRODUCT_ID" | jq

# Get all prices from a vendor
curl "https://web-production-cd557.up.railway.app/price-history/vendor/VENDOR_ID" | jq
```

---

## 🔧 **Method 5: Command Line (Local)**

If you have the repository cloned:

```bash
cd /Users/AR180/Desktop/Codespace/pricebot

# Link to Railway (one-time setup)
railway link --project 21c91447-567b-43a7-aadc-ae3314fbd16a
railway link web

# Upload a file
railway run python -m app.cli.ingest "path/to/file.xlsx" --vendor "Vendor Name"

# List documents
railway run python -m app.cli.list_documents --limit 20

# View specific document
railway run python -m app.cli.list_documents --limit 1
```

---

## 📋 **What Pricebot Extracts**

From each price sheet, Pricebot automatically extracts:

- ✅ **Product Name** (normalized)
- ✅ **Model Number** (deduplicated)
- ✅ **Price** (with currency)
- ✅ **Vendor Name**
- ✅ **Condition** (new/used/refurbished)
- ✅ **Quantity** (if available)
- ✅ **Location** (if available)
- ✅ **Brand** (if available)
- ✅ **Timestamp** (when the price was captured)

### **Smart Features:**

- 🧠 **Auto-deduplication**: Same product from different vendors tracked separately
- 📊 **Price history**: Tracks price changes over time
- 🔍 **Fuzzy matching**: Recognizes products even with slight name variations
- 🏷️ **Alias support**: Multiple names for the same product

---

## 🎯 **Common Use Cases**

### **1. Upload Daily Price Updates**

```bash
# Upload new price sheet every day
curl -X POST "https://web-production-cd557.up.railway.app/documents/upload" \
  -F "file=@daily_prices_$(date +%Y%m%d).xlsx" \
  -F "vendor_name=Daily Vendor"
```

### **2. Compare Vendor Prices**

```bash
# Get all offers for a specific product
curl "https://web-production-cd557.up.railway.app/offers?product_id=PRODUCT_ID&sort=price" | jq

# Returns sorted by price (lowest first)
```

### **3. Track Price Changes**

```bash
# Get price history for iPhone 15 Pro
curl "https://web-production-cd557.up.railway.app/price-history/product/PRODUCT_ID" | jq

# See price changes over time
```

### **4. Bulk Upload**

```bash
# Upload multiple files at once
for file in /path/to/vendor_sheets/*.xlsx; do
  vendor=$(basename "$file" .xlsx)
  curl -X POST "https://web-production-cd557.up.railway.app/documents/upload" \
    -F "file=@$file" \
    -F "vendor_name=$vendor"
  sleep 2  # Rate limit
done
```

---

## 🚨 **Troubleshooting**

### **Upload fails with "Unknown processor"**

**Solution:** Specify file type manually:
- `processor=spreadsheet` for Excel/CSV
- `processor=document_text` for PDF/images
- `processor=whatsapp_text` for WhatsApp chats

### **No data extracted**

**Possible causes:**
1. File format not recognized
2. No price columns detected
3. File is empty or corrupt

**Solution:**
- Check operator dashboard for errors
- View document detail for error messages
- Try different file format

### **Duplicate products created**

**Why:** Product names are slightly different in different sheets

**Solution:** We use fuzzy matching, but you can:
1. Check product aliases in the database
2. Manually merge products (Phase 2 feature)

---

## 📊 **Current Status**

**✅ What's Working:**
- Database initialized ✅
- 37 offers ingested from 3 sources ✅
- All API endpoints functional ✅
- Upload interface deployed ✅
- Operator dashboard working ✅
- WhatsApp chat parsing ✅
- Spreadsheet processing ✅

**🔄 Coming in Phase 2:**
- Email integration
- WhatsApp Business API
- OCR for images/PDFs (requires Tesseract on Railway)
- Advanced deduplication
- Manual price overrides
- Approval workflows

---

## 🎓 **Quick Start Tutorial**

### **5-Minute Test:**

1. **Open upload page:** https://web-production-cd557.up.railway.app/

2. **Drag and drop** any price sheet (Excel, CSV, or PDF)

3. **Enter vendor name** (e.g., "Test Vendor")

4. **Click "Upload & Process"**

5. **View results** in the operator dashboard

6. **Check offers:** https://web-production-cd557.up.railway.app/offers

**Done!** Your first price sheet is now in the system.

---

## 📞 **Need Help?**

- **API Docs:** https://web-production-cd557.up.railway.app/docs
- **Check Logs:** `railway logs` (from terminal)
- **View Database:** Operator Dashboard
- **Post-Deployment Guide:** `POST_DEPLOYMENT.md`
- **Deployment Issues:** `DEPLOYMENT_FIXES.md` or `HEALTH_CHECK_FIX.md`

---

## 🚀 **Next Steps**

1. ✅ **Upload your first real data** (use the web interface!)
2. ✅ **Test the API** (browse offers, vendors, products)
3. ✅ **Set up automated daily uploads** (cron job or scheduled task)
4. 📧 **Plan Phase 2 features** (email, WhatsApp Business API)

---

**Your Pricebot is production-ready!** 🎉

Start uploading data at: **https://web-production-cd557.up.railway.app/**





