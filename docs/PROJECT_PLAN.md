# Pricebot Project Plan & Roadmap

**Project Owner:** AR180 (itsAR-VR)  
**Project Start Date:** September 23, 2025  
**Current Version:** 0.1.0  
**Status:** Phase 1 Complete, Phase 2 In Progress  

---

## Executive Summary

Pricebot is a price intelligence backend for Cellntell that ingests vendor pricing data from multiple formats (Excel, CSV, WhatsApp transcripts, PDFs, images), normalizes the information into a structured database, and exposes REST APIs for price queries, history tracking, and product/vendor discovery. The system aims to eliminate manual price tracking, enable competitive analysis, and provide a foundation for chatbot-driven price recommendations.

**Current State:** Core ingestion pipeline operational with 3 file sources (spreadsheet, WhatsApp, OCR/PDF), 37 total offers ingested across 3 documents, test suite passing (14/14), and foundation for Railway deployment in place.

**Target Completion:** **October 31, 2025** (5 weeks from today)

---

## 1. Current Status Assessment (As of Sep 30, 2025)

### ✅ Completed Features

| Component | Status | Notes |
|-----------|--------|-------|
| **Core Data Model** | ✅ Complete | Vendors, Products, Offers, PriceHistory, SourceDocuments, ProductAliases, IngestionJobs all defined with SQLModel |
| **Spreadsheet Ingestion** | ✅ Complete | Auto-detects headers, maps common column aliases (price, qty, SKU, UPC), handles headerless CSVs |
| **WhatsApp Text Parser** | ✅ Complete | Regex-based extraction for chat exports; filters reactions/noise; assigns per-speaker vendors |
| **OCR/PDF Document Processor** | ✅ Complete | pytesseract for images, pypdf for PDFs; requires system Tesseract install |
| **CLI Ingestion Tool** | ✅ Complete | `app.cli.ingest` with vendor override, processor selection, auto-storage to `storage/` |
| **CLI List Documents** | ⚠️ Partial | Implemented but has session detachment bug (needs lazy loading fix) |
| **REST API - Offers** | ✅ Complete | `GET /offers` with filters (vendor, product, since, limit) |
| **REST API - Products** | ✅ Complete | `GET /products`, `GET /products/{id}` with search and offer counts |
| **REST API - Vendors** | ✅ Complete | `GET /vendors`, `GET /vendors/{id}` with search and offer counts |
| **REST API - Price History** | ✅ Complete | `GET /price-history/product/{id}`, `GET /price-history/vendor/{id}` |
| **REST API - Documents** | ✅ Complete | `GET /documents`, `GET /documents/{id}` with offer details |
| **Operator UI** | ✅ Complete | HTML dashboard at `/admin/documents` with status filtering, drill-down to offers |
| **Testing** | ✅ Complete | 14 passing tests covering ingestion, service layer, API routes, and UI |
| **Local Development** | ✅ Complete | SQLite backing, `.env` config, venv setup documented |
| **Repository & Docs** | ✅ Complete | README, schema docs, deployment guide, ingestion playbook, project scope |

### ⚠️ Known Issues & Gaps

| Issue | Severity | Impact | Priority |
|-------|----------|--------|----------|
| CLI `list_documents` session detachment | Medium | Blocks CLI audit workflow | **P0** |
| Tesseract not installed (macOS) | Low | OCR ingestion fails locally | P1 |
| No price history materialization logic | High | History API returns empty results | **P0** |
| Missing product alias matching | High | Duplicate products from name variations | **P0** |
| No LLM fallback for OCR/chat | Medium | Complex price sheets fail silently | P1 |
| Object storage not wired (using local FS) | Medium | Artifacts lost on Railway restarts | P1 |
| No Railway deployment yet | High | No production instance | **P0** |
| No Alembic migrations | Medium | Schema changes require manual rebuild | P1 |
| Missing API documentation narrative | Low | Only auto-generated OpenAPI | P2 |
| No RBAC/auth on operator UI | Medium | Console is publicly accessible | P1 |
| Frontend chatbot not built | High | No user-facing interface | P2 |

---

## 2. Gap Analysis

### Critical Gaps (Must Fix for MVP)

1. **Price History Logic Not Implemented**  
   - **Current:** `price_history` table exists but no business logic populates it  
   - **Required:** When new offer arrives, close prior span (`valid_to = new_offer.captured_at`) and open new span  
   - **ETA:** 2 days  

2. **Product Deduplication Missing**  
   - **Current:** Each ingestion creates new products even for same SKU/name  
   - **Required:** Match by UPC > model_number > canonical_name; create aliases for vendor-specific names  
   - **ETA:** 3 days  

3. **Railway Production Deployment**  
   - **Current:** Only local SQLite instance  
   - **Required:** Deploy to Railway with Postgres, persistent volume, cron jobs  
   - **ETA:** 2 days  

4. **CLI Session Bug**  
   - **Current:** `list_documents` throws DetachedInstanceError  
   - **Required:** Eagerly load relationships or materialize data before session close  
   - **ETA:** 1 day  

### Important Gaps (Phase 2 Polish)

5. **LLM Enrichment for OCR/Chat**  
   - Add OpenAI/Anthropic structured output prompts when Tesseract OCR or regex parsing fails  
   - **ETA:** 3 days  

6. **Object Storage Integration**  
   - Replace local `storage/` with Railway persistent volume or S3  
   - **ETA:** 2 days  

7. **Alembic Migration Setup**  
   - Generate initial migration, test upgrade/downgrade  
   - **ETA:** 1 day  

8. **Operator UI Auth**  
   - Add Railway session auth or basic HTTP auth to `/admin/*`  
   - **ETA:** 1 day  

### Nice-to-Have (Phase 3+)

9. **Vercel Chatbot Frontend**  
   - Natural language price queries → API calls → formatted responses  
   - **ETA:** 5 days  

10. **Embedding-Based Product Search**  
    - Generate embeddings for aliases and product descriptions for fuzzy matching  
    - **ETA:** 3 days  

11. **Automated Nightly Ingestion**  
    - Railway cron jobs + file drop automation  
    - **ETA:** 2 days  

---

## 3. Development Roadmap

### Phase 1: Core Foundations ✅ (Complete)
**Duration:** Sep 23 - Sep 30, 2025 (1 week)  
**Goal:** Establish data model, basic ingestion, API skeleton, and test coverage.

- [x] Define SQLModel schema (vendors, products, offers, price_history, source_documents)
- [x] Build spreadsheet ingestion processor with heuristics
- [x] Implement CLI for local file ingestion
- [x] Create REST API routes for offers, products, vendors, documents
- [x] Add operator UI dashboard
- [x] Write unit/integration tests (14 tests)
- [x] Document schema, deployment, and ingestion workflows

**Outcomes:** 37 offers ingested, 14/14 tests passing, deployment-ready Procfile/railway.json.

---

### Phase 2: Intelligence & Production Hardening 🚧 (In Progress)
**Duration:** Oct 1 - Oct 14, 2025 (2 weeks)  
**Goal:** Fix critical gaps, deploy to Railway, activate price history, implement product matching.

#### Week 1 (Oct 1-7): Critical Fixes
- [ ] **Day 1-2:** Fix `list_documents` CLI session bug; add eager loading
- [ ] **Day 3-5:** Implement price history materialization service
  - [ ] Service method to close prior span when new offer arrives
  - [ ] Trigger on `OfferIngestionService.ingest()`
  - [ ] Add integration test for history updates
- [ ] **Day 6-7:** Build product deduplication logic
  - [ ] Match by UPC → model_number → canonical_name
  - [ ] Auto-create aliases for vendor-specific names
  - [ ] Add test for duplicate prevention

**Deliverables:**
- ✅ Price history API returns real data
- ✅ Products deduplicated across vendors
- ✅ CLI tools operational

#### Week 2 (Oct 8-14): Production Deployment
- [ ] **Day 8-9:** Railway deployment
  - [ ] Create Railway project, provision Postgres
  - [ ] Configure environment variables (DATABASE_URL, storage volume)
  - [ ] Deploy via `railway up`, verify health checks
  - [ ] Run initial migration (`init_db()` remotely)
- [ ] **Day 10-11:** Persistent storage setup
  - [ ] Mount Railway volume to `/data/storage`
  - [ ] Update config to use volume path
  - [ ] Test artifact persistence across deploys
- [ ] **Day 12:** LLM enrichment prototype
  - [ ] Add OpenAI structured output fallback in `document.py`
  - [ ] Test with complex price sheet
- [ ] **Day 13-14:** Alembic migrations + operator auth
  - [ ] Generate initial Alembic revision
  - [ ] Add HTTP basic auth to `/admin/*` routes
  - [ ] Update deployment docs

**Deliverables:**
- ✅ Production instance live on Railway with Postgres
- ✅ Artifacts persisted to Railway volume
- ✅ Operator UI secured
- ✅ Alembic migration framework ready

**Milestone:** **MVP Production Ready** (Oct 14, 2025)

---

### Phase 3: Query Layer & Chatbot Integration 📋 (Planned)
**Duration:** Oct 15 - Oct 28, 2025 (2 weeks)  
**Goal:** Enhance query capabilities, build chatbot frontend, enable conversational price discovery.

#### Week 3 (Oct 15-21): Enhanced Retrieval
- [ ] Add `/prices/search` endpoint with fuzzy product matching
- [ ] Implement embedding-based alias search (OpenAI embeddings)
- [ ] Add price trend analysis endpoint (7-day, 30-day averages)
- [ ] Build manual alias override UI in operator dashboard
- [ ] Add price alert service (notify when price drops below threshold)

**Deliverables:**
- ✅ Advanced search API with alias resolution
- ✅ Operator tools for alias management

#### Week 4 (Oct 22-28): Vercel Chatbot Frontend
- [ ] Create Next.js app on Vercel
- [ ] Build conversational UI (input → LLM → API → response)
- [ ] Integrate with Pricebot API (search, history, vendor comparison)
- [ ] Add authentication (NextAuth.js)
- [ ] Deploy to Vercel, configure CORS on Railway backend

**Deliverables:**
- ✅ Public chatbot interface for price queries
- ✅ End-to-end workflow: vendor sheet upload → API ingestion → chatbot query → results

**Milestone:** **Public Beta Launch** (Oct 28, 2025)

---

### Phase 4: Automation & Operations 🔄 (Future)
**Duration:** Oct 29 - Nov 11, 2025 (2 weeks)  
**Goal:** Automate recurring ingestion, add monitoring, optimize performance.

- [ ] Set up Railway cron jobs for nightly vendor sheet imports
- [ ] Integrate WhatsApp Business API for automatic chat export
- [ ] Add Sentry error tracking and health monitoring
- [ ] Optimize database queries (add indexes, query analysis)
- [ ] Build BI export (price history → CSV/Excel for Tableau)
- [ ] Create admin dashboard for user management and audit logs
- [ ] Write operational runbooks (SLOs, escalation, incident response)

**Milestone:** **Production Stable v1.0** (Nov 11, 2025)

---

## 4. Resource Planning

### Team Composition
- **Lead Developer:** AR180 (full-stack, backend focus)
- **Supporting Roles:** None currently assigned
- **Required Skills:**
  - Python (FastAPI, SQLModel, pytest)
  - SQL (PostgreSQL, query optimization)
  - Frontend (Next.js, React) for chatbot phase
  - DevOps (Railway, Docker, CI/CD)
  - LLM integration (OpenAI API, prompt engineering)

### Infrastructure
- **Development:** Local macOS, SQLite, venv
- **Staging:** Railway service with Postgres (planned)
- **Production:** Railway service + Vercel frontend (planned)
- **Estimated Monthly Costs:**
  - Railway Postgres: $10/month
  - Railway compute: $5/month (base tier)
  - Vercel hosting: Free tier (hobby)
  - OpenAI API: ~$20/month (estimated usage)
  - **Total:** ~$35/month

### Tools & Dependencies
- **Core:** FastAPI, SQLModel, Pandas, Pydantic, Uvicorn
- **Ingestion:** pytesseract, pypdf, openpyxl, xlrd
- **LLM:** openai (optional)
- **Testing:** pytest, httpx, pytest-cov
- **Deployment:** Railway CLI, Alembic, Docker (optional)
- **Frontend:** Next.js, TailwindCSS, NextAuth.js (Phase 3)

---

## 5. Risk Management

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| **OCR accuracy too low for complex sheets** | Medium | High | Add LLM fallback with structured prompts; manual review via operator UI |
| **Product deduplication creates false matches** | Medium | Medium | Implement manual override UI; log all merge decisions for audit |
| **Railway costs exceed budget** | Low | Medium | Monitor usage; optimize queries; consider SQLite for low-traffic periods |
| **LLM API rate limits** | Low | Low | Implement retry with exponential backoff; cache responses |
| **Frontend chatbot scope creep** | High | Medium | Define MVP feature set; defer advanced features to Phase 4+ |
| **Vendor data format changes break ingestion** | Medium | High | Version processors; add regression tests for each vendor; alert on parse failures |
| **No frontend developer available for Phase 3** | Medium | High | Simplify chatbot to API-only; document API for third-party integration |

---

## 6. Quality Assurance Plan

### Testing Strategy
- **Unit Tests:** All ingestion processors, service methods, utilities (current: 14 tests)
- **Integration Tests:** API endpoints with live database fixture
- **E2E Tests:** CLI ingestion → API query → operator UI verification
- **Load Tests:** Simulate 1000 offers/minute ingestion (Phase 4)
- **Coverage Target:** 80% code coverage by Phase 2 end

### Code Quality
- **Linting:** Ruff (line-length=100)
- **Type Checking:** Mypy (optional, not enforced yet)
- **Pre-commit Hooks:** Planned for Phase 2 (black, ruff, pytest)
- **Code Reviews:** Self-review via GitHub PR workflow

### Deployment QA
- **Staging Environment:** Railway service with separate Postgres DB
- **Smoke Tests:** Health endpoint, sample API calls, CLI ingestion run
- **Rollback Plan:** Railway supports instant rollback to previous deploy
- **Monitoring:** Health checks every 60s (configured in `railway.json`)

---

## 7. Communication & Stakeholder Plan

### Stakeholders
- **Primary:** AR180 (owner, developer)
- **Secondary:** Cellntell team (end users, price analysts)
- **Future:** External clients (chatbot users)

### Communication Cadence
- **Weekly Status Updates:** GitHub project board + summary in README
- **Milestone Reviews:** After each phase completion
- **Incident Reports:** Real-time via Slack/Discord (if integrated)
- **Documentation Updates:** Continuous (commit with code changes)

### Success Metrics
| Metric | Current | Target (MVP) | Target (v1.0) |
|--------|---------|--------------|---------------|
| Offers Ingested | 37 | 500+ | 10,000+ |
| Active Vendors | 3 | 10+ | 50+ |
| Unique Products | ~20 | 200+ | 2,000+ |
| API Response Time (p95) | N/A | <200ms | <100ms |
| Ingestion Success Rate | ~85% | 95% | 98% |
| Test Coverage | ~60% | 80% | 85% |
| Uptime (Production) | N/A | 99% | 99.5% |

---

## 8. Detailed Timeline & Milestones

```
Sep 23-30, 2025    Phase 1: Core Foundations ✅
├─ Data model, ingestion, API, tests
└─ Milestone: Local MVP functional

Oct 1-7, 2025      Phase 2 Week 1: Critical Fixes 🚧
├─ Fix CLI session bug
├─ Implement price history logic
└─ Build product deduplication

Oct 8-14, 2025     Phase 2 Week 2: Production Deploy 🚧
├─ Railway deployment with Postgres
├─ Persistent storage setup
├─ Alembic migrations + auth
└─ Milestone: MVP Production Ready ⭐

Oct 15-21, 2025    Phase 3 Week 1: Enhanced Retrieval 📋
├─ Fuzzy search API
├─ Embedding-based aliases
└─ Manual override UI

Oct 22-28, 2025    Phase 3 Week 2: Chatbot Frontend 📋
├─ Next.js app on Vercel
├─ Conversational UI + API integration
└─ Milestone: Public Beta Launch ⭐

Oct 29-Nov 11      Phase 4: Automation & Ops 🔄
├─ Cron jobs + monitoring
├─ Performance optimization
└─ Milestone: Production Stable v1.0 ⭐
```

**Target Completion Date:** **October 31, 2025**  
*(MVP Production Ready + Public Beta; v1.0 stable by Nov 11)*

---

## 9. Next Immediate Actions (This Week: Oct 1-7)

### Priority P0 Tasks
1. **Fix CLI list_documents bug** (1 day)
   - Add `joinedload` for relationships in query
   - Test with current DB state
   - Commit fix + add regression test

2. **Implement price history materialization** (3 days)
   - Create `PriceHistoryService` with `update_history(offer)` method
   - Call from `OfferIngestionService.ingest()`
   - Add tests for span closing logic
   - Verify API returns real data

3. **Build product deduplication** (3 days)
   - Enhance `_get_or_create_product()` with UPC/model matching
   - Create aliases for vendor-specific names
   - Add test with duplicate sheet ingestion
   - Document deduplication rules in schema docs

### Success Criteria for Week 1
- ✅ `python -m app.cli.list_documents --limit 20` runs without error
- ✅ `GET /price-history/product/{id}` returns populated history spans
- ✅ Re-ingesting same product under different vendor creates alias, not duplicate
- ✅ All tests passing (target: 18+ tests)

---

## 10. Dependencies & Blockers

### External Dependencies
- **Tesseract OCR:** Required for image ingestion (install via `brew install tesseract`)
- **Railway Account:** Free tier available, credit card for production
- **OpenAI API Key:** Optional for LLM enrichment (Phase 2+)
- **Vercel Account:** Required for frontend (Phase 3)

### Current Blockers
- ❌ No production instance (blocks stakeholder demo)  
  **Resolution:** Deploy to Railway by Oct 14  

- ❌ Price history empty (blocks historical analysis)  
  **Resolution:** Implement materialization logic by Oct 7  

- ❌ Product duplicates (blocks accurate inventory)  
  **Resolution:** Implement deduplication by Oct 7  

### Future Risks
- WhatsApp Business API integration complexity (Phase 4)
- Frontend resource availability (Phase 3)
- LLM costs scaling with usage (Phase 2+)

---

## 11. Budget & Cost Estimates

### Development Costs (Time Investment)
- **Phase 1:** 40 hours (complete)
- **Phase 2:** 80 hours (2 weeks × 40h)
- **Phase 3:** 80 hours (2 weeks × 40h)
- **Phase 4:** 80 hours (2 weeks × 40h)
- **Total Estimated:** 280 hours (~7 weeks @ 40h/week)

### Infrastructure Costs (Monthly)
| Service | Plan | Cost |
|---------|------|------|
| Railway Postgres | Starter | $10 |
| Railway Compute | Hobby | $5 |
| Railway Storage | 1GB | Included |
| Vercel Hosting | Hobby | $0 |
| OpenAI API | Pay-as-you-go | ~$20 |
| **Total** | | **~$35/month** |

### One-Time Costs
- Domain registration (optional): $12/year
- SSL certificate: $0 (Railway/Vercel include)

---

## 12. Success Criteria & Acceptance

### MVP Acceptance Criteria (Oct 14, 2025)
- ✅ Production instance accessible via Railway URL
- ✅ At least 500 offers ingested from 10+ vendors
- ✅ Price history API returns accurate spans for all products
- ✅ Operator UI secured with authentication
- ✅ API documentation available at `/docs`
- ✅ Test coverage ≥80%
- ✅ Uptime >99% over 7 days
- ✅ CLI tools operational for manual ingestion

### Public Beta Criteria (Oct 28, 2025)
- ✅ Chatbot frontend live on Vercel
- ✅ End-users can query prices conversationally
- ✅ At least 1,000 offers ingested
- ✅ Average API response time <200ms
- ✅ Ingestion success rate >95%

### v1.0 Stable Criteria (Nov 11, 2025)
- ✅ Automated nightly ingestion running
- ✅ Monitoring and alerts configured
- ✅ Operational runbooks documented
- ✅ BI export functional
- ✅ 10,000+ offers ingested
- ✅ Uptime >99.5% over 30 days

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | Sep 30, 2025 | AR180 | Initial project plan created |

---

**Document Owner:** AR180  
**Last Updated:** September 30, 2025  
**Next Review:** October 7, 2025 (after Phase 2 Week 1)
