# Pricebot Help Topics

## AI normalization

AI normalization standardizes product names, vendors, and attribute spelling when documents are re-ingested. It is best used on spreadsheets whose column headers map imperfectly to the template. When triggered, the pipeline records whether the `llm_extractor` or heuristics produced each normalized row and flags suspect headers in diagnostics.

## prefer_llm toggle

The Prefer LLM toggle forces Pricebot to use the large language model extractor, even when the heuristic extractor is confident. Enable it from the chat composer when you need richer normalization or when source files differ significantly from the template.

## Re-ingest route

Re-ingestion lets you push a document back through parsing and AI normalization. Call `POST /documents/{document_id}/ingest` (optionally with `{"force": true}`) or use the “Re-ingest now” quick action in the chat diagnostics card. The job re-uses stored uploads, so you do not need to upload the file again.

## Related docs endpoint

Pricebot can fetch documents related to a product or vendor with `/documents/{document_id}/related`. The chat UI shows “Related documents” chips after a summary so you can explore neighboring uploads without leaving the conversation.

## Template download & columns

Download the canonical spreadsheet template from the diagnostics header or `/documents/templates/vendor-price`. The sheet includes columns for `Item`, `Price`, `Qty`, `Condition`, `Location`, and `Notes`. Matching these names keeps ingestion fast and reduces normalization time.

## Enabling OpenAI

Set the `ENABLE_OPENAI` flag and provide `OPENAI_API_KEY` to unlock AI normalization, LLM-powered help answers, and richer spreadsheet extraction. In production, rotate the key regularly and verify the environment flag before redeploying.
