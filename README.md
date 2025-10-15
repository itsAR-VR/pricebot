# Pricebot - Price Intelligence Backend

**Production-ready price intelligence system for electronics vendors.**  
Ingest vendor pricing from multiple sources → normalize → query via REST API.

[![GitHub](https://img.shields.io/badge/github-itsAR--VR%2Fpricebot-blue)](https://github.com/itsAR-VR/pricebot)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-green.svg)](https://fastapi.tiangolo.com)
[![Tests](https://img.shields.io/badge/tests-14%2F14%20passing-brightgreen)](./tests)

---

## 🚀 Quickstart (5 minutes)

```bash
# Clone and setup
git clone https://github.com/itsAR-VR/pricebot && cd pricebot
python -m venv .venv && source .venv/bin/activate
pip install -e '.[ocr,pdf,dev]'

# Start API server
uvicorn app.main:app --reload
# → http://localhost:8000 (API)
# → http://localhost:8000/docs (Swagger)
# → http://localhost:8000/admin/documents (Operator UI)

# Ingest your first price sheet
python -m app.cli.ingest vendor_prices.xlsx --vendor "Vendor Name"
```

**[📖 Full Quickstart Guide](docs/QUICKSTART.md)** | **[🔌 API Reference](docs/API_REFERENCE.md)** | **[🆘 Help Topics](docs/HELP_TOPICS.md)** | **[📋 Project Plan](docs/PROJECT_PLAN.md)**

---

## ✅ Features (MVP Complete)

### Ingestion Processors
- ✅ **Spreadsheets** - Excel/CSV with auto-schema detection
- ✅ **WhatsApp** - Chat transcript parsing with optional GPT fallback
- ✅ **OCR/PDF** - Images/PDFs with GPT vision OCR fallback (pypdf + OpenAI)

### Data Management
- ✅ **Product Deduplication** - Match by UPC → SKU → name
- ✅ **Price History** - Automatic span tracking with change detection
- ✅ **Vendor Normalization** - Alias management per source
- ✅ **Document Traceability** - Link offers back to source files

### APIs
- ✅ **Offers** - Query with filters (vendor, product, date)
- ✅ **Products** - Search by name/UPC/model, view history
- ✅ **Vendors** - List with offer counts
- ✅ **Price History** - Time-series data per product/vendor
- ✅ **Documents** - Audit ingested source files

### Operations
- ✅ **Operator UI** - Web dashboard at `/admin/documents`
- ✅ **CLI Tools** - Ingest and audit commands
- ✅ **Test Coverage** - 14/14 tests passing

## Local Development
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload
```

### CLI Ingestion
Parse source files and persist offers into the local database:
```bash
# Excel/CSV
python -m app.cli.ingest "../Raw Data from Abdursajid.xlsx" --vendor "Raw Vendor"

# WhatsApp text export (force the chat parser)
python -m app.cli.ingest WAbot/whatsapp_business_chat_data.txt --processor whatsapp_text

# Image or PDF price sheet (requires `pip install -e .[ocr,pdf]` plus `ENABLE_OPENAI=true` if you want GPT OCR)
python -m app.cli.ingest vendor_sheet.png --processor document_text --vendor "Sample Vendor"

# Review ingested documents
python -m app.cli.list_documents --limit 20
```

List registered processors:
```bash
python - <<'PY'
from app.ingestion import registry
print(sorted(registry.processors))
PY
```

The default configuration uses a local SQLite database (`pricebot.db`). Override settings via environment variables as defined in `app/core/config.py`.

### LLM Normalization
To enable the non-structured ingestion pipeline, configure OpenAI credentials before running the API or CLI:

```bash
export ENABLE_OPENAI=true
export OPENAI_API_KEY=sk-your-key
```

With the flag enabled, document, WhatsApp, and spreadsheet processors fall back to GPT-based extraction when heuristics miss price lines. Use `--option prefer_llm=true` with the CLI to force AI parsing for specific runs (spreadsheets will still merge heuristic-only rows when the LLM skips them).

### Testing
```bash
pip install -e .[dev]
pytest
```

---

## 🚢 Deploy to Railway (5 minutes)

```bash
# 1. Login
railway login

# 2. Create project and add database
railway init
railway add postgresql

# 3. Deploy
railway up

# 4. Initialize database
railway run python -c "from app.db.session import init_db; init_db()"

# 5. Get your URL
railway domain
```

**Your API is now live!** Visit `https://your-app.up.railway.app/docs`

**[📖 Deployment Guide](docs/deployment_railway.md)** | **[⚡ Quickstart](docs/QUICKSTART.md)**

---

## 📊 Current Status

**Version:** 0.1.0 (MVP)  
**Data Ingested:** 543 products, 37 offers, 20 documents  
**API Endpoints:** 11 routes across 6 categories  
**Test Coverage:** 16/16 passing  
**Ready for:** Production deployment

**Next Milestones:**
- Oct 14: MVP Production Ready ⭐
- Oct 28: Public Beta Launch
- Nov 11: v1.0 Production Stable

See **[PROJECT_PLAN.md](docs/PROJECT_PLAN.md)** for full roadmap.

## 🗨️ Conversational Interface (Design)
- Chat workspace spec (Cursor-style UI, GPT-5 orchestration, tool registry) captured in **[CHAT_INTERFACE_SPEC.md](docs/CHAT_INTERFACE_SPEC.md)**.
- Spreadsheet upload template defined from real vendor sheets; see the "Spreadsheet Template Guidelines" section for required headers and formatting rules.
- Upcoming chat release will surface download links for the template and live ingestion status inside the uploader.

---

## 📁 Repository Structure

```
pricebot/
├── app/
│   ├── api/          # FastAPI routes (offers, products, vendors, etc.)
│   ├── cli/          # CLI tools (ingest, list_documents)
│   ├── core/         # Config and settings
│   ├── db/           # SQLModel models and session
│   ├── ingestion/    # Processors (spreadsheet, whatsapp, OCR)
│   ├── services/     # Business logic (offer ingestion, history)
│   ├── templates/    # HTML templates for operator UI
│   └── ui/           # Web views (operator dashboard)
├── docs/             # Documentation
│   ├── QUICKSTART.md
│   ├── API_REFERENCE.md
│   ├── PROJECT_PLAN.md
│   └── deployment_railway.md
├── tests/            # Test suite (14 tests)
├── storage/          # Ingested file artifacts
├── pyproject.toml    # Dependencies and config
├── railway.json      # Railway deployment config
└── Procfile          # Production startup command
```

### Operator UI
- Visit `http://localhost:8000/admin/documents` to monitor artefacts, statuses, and extracted offers.
- Filter by status, drill into document details, and inspect raw ingestion metadata.
- In production the same console is available at `https://<your-domain>/admin/documents` (protect with your platform’s auth solution).

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`pytest`)
5. Commit (`git commit -m 'feat: add amazing feature'`)
6. Push (`git push origin feature/amazing-feature`)
7. Open a Pull Request

**Coding Standards:**
- Python 3.11+ with type hints
- Ruff for linting (line-length=100)
- pytest for testing (maintain >80% coverage)
- Conventional commits for messages

---

## 📄 License

This project is proprietary software for Cellntell. All rights reserved.

---

## 📞 Support

- **Issues:** [GitHub Issues](https://github.com/itsAR-VR/pricebot/issues)
- **Documentation:** [docs/](./docs)
- **Owner:** AR180 (itsAR-VR)

---

**Built with:** FastAPI • SQLModel • Pandas • Tesseract OCR • Railway
