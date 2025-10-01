# Goal
Fix the document upload functionality in pricebot that is currently not working. The upload button/endpoint may have database, backend, or frontend issues preventing successful file uploads.

# Definition of Done
- [x] Upload button is clickable and triggers file dialog
- [x] File uploads successfully reach the backend `/documents/upload` endpoint
- [x] Backend processes files without database errors
- [x] Files are persisted to storage correctly
- [x] Database records are created for uploaded documents
- [x] Frontend displays success/failure feedback correctly
- [x] All tests pass (pytest)
- [x] Changes committed and pushed to git
- [ ] Railway deployment succeeds with new changes

# Context & Background

## Current Issues
- Upload function is not working (user confirmed)
- Possible causes: database, backend endpoint, frontend interaction
- Previous fixes attempted: upload button HTML structure, endpoint implementation
- GPT-5 OCR integration is in place but upload may be failing before OCR processing

## Tech Stack
- **Backend**: FastAPI, SQLModel, SQLAlchemy
- **Database**: PostgreSQL (Railway)
- **Frontend**: HTML/JavaScript (upload.html)
- **Storage**: Local filesystem (`storage/` directory)
- **OCR**: GPT-4o vision API (OpenAI)

## Key Files
- `app/api/routes/documents.py` - Upload endpoint
- `app/templates/upload.html` - Upload UI
- `app/db/models.py` - Database models
- `app/ingestion/document.py` - OCR processor
- `app/services/offers.py` - Offer ingestion service

## Recent Changes
- Migrated from Tesseract to GPT-5 OCR
- Fixed upload button HTML structure (changed button to label)
- Added POST /documents/upload endpoint
- Fixed OfferService import to OfferIngestionService

# Debugging Strategy
1. **Check database connection** - Verify PostgreSQL is reachable and schema is initialized
2. **Test upload endpoint** - Use curl/Postman to test POST /documents/upload directly
3. **Check frontend logs** - Browser console for JavaScript errors
4. **Check backend logs** - FastAPI/uvicorn logs for exceptions
5. **Test file write permissions** - Ensure storage/ directory is writable
6. **Verify session handling** - Check SQLAlchemy session lifecycle in upload endpoint
7. **Test with minimal file** - Try uploading a simple CSV first

# Implementation Checklist
- [x] Add debug logging to upload endpoint
- [x] Test database connection and schema
- [x] Verify storage directory exists and is writable
- [x] Test upload endpoint with curl (bypass frontend)
- [x] Fix any database session/transaction issues
- [x] Fix any file I/O errors
- [x] Fix any frontend JavaScript errors
- [x] Add error handling and user-friendly messages
- [x] Test end-to-end upload flow
- [x] Add smoke test for upload endpoint
- [x] Document the fix in UPLOAD_FIX.md

# Acceptance Criteria
User can successfully upload a file (CSV, image, or PDF) through the web interface, see a success message, and verify the file appears in the database and storage directory.

# Notes
- OpenAI API key is already configured: `OPENAI_API_KEY`
- Railway deployment URL: `https://web-production-cd557.up.railway.app/`
- Local dev: `cd /Users/AR180/Desktop/Codespace/pricebot && source .venv/bin/activate && uvicorn app.main:app --reload`
- Test command: `pytest`
- Database is Railway PostgreSQL (check connection in Railway dashboard)
