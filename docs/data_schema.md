# Pricing Data Schema Draft

## Source Formats
- **Structured spreadsheets**: `.xlsx`, `.xls`, `.csv` (e.g. `Raw Data from Abdursajid.xlsx`, `SB Technology Pricelist Oct 25.xlsx`).
- **Semi-structured exports**: WhatsApp chat text dumps where price offers appear inside free-form messages.
- **Unstructured documents**: PDF price sheets and image snapshots shared in chat (require OCR + text parsing).

## Normalized Storage Model

### vendors
| column | type | notes |
| --- | --- | --- |
| id | UUID | Primary key |
| name | text | Display name, deduplicated |
| contact_info | jsonb | Emails, phone numbers, WhatsApp handles |
| metadata | jsonb | Optional, allows tagging (e.g. region) |
| created_at | timestamptz | |
| updated_at | timestamptz | |

### products
| column | type | notes |
| --- | --- | --- |
| id | UUID | Primary key |
| canonical_name | text | Human friendly name (e.g. "iPhone 15 Pro 256GB") |
| brand | text | Normalized manufacturer |
| model_number | text | OEM model or SKU (e.g. `RT-IP1164GBBK-UN-RW-FE`) |
| upc | text | From price lists such as `SB Technology Pricelist Oct 25.xlsx` |
| category | text | Laptop / Smartphone / Accessory etc. |
| spec | jsonb | Arbitrary attributes (storage, color, condition) |
| created_at | timestamptz | |
| updated_at | timestamptz | |

### product_aliases
Captures raw strings observed in documents to aid matching.
| column | type | notes |
| --- | --- | --- |
| id | UUID | |
| product_id | UUID | FK to `products` |
| alias_text | text | Raw description (`"ECHO POP"`, `"S24 Ultra 512 GB"`) |
| source_vendor_id | UUID | Enables vendor-specific aliases |
| embedding | vector | Optional future field for semantic search |
| created_at | timestamptz | |

### source_documents
Records every uploaded artefact.
| column | type | notes |
| --- | --- | --- |
| id | UUID | |
| vendor_id | UUID | FK |
| file_name | text | Original filename |
| file_type | text | `excel`, `pdf`, `image`, `text` |
| storage_path | text | Location in object storage (Railway bucket, S3, etc.) |
| ingest_started_at | timestamptz | |
| ingest_completed_at | timestamptz | |
| status | text | `pending`/`processed`/`failed` |
| metadata | jsonb | Hashes, OCR confidence, etc. |

### offers
Represents a price quote captured from a document.
| column | type | notes |
| --- | --- | --- |
| id | UUID | |
| product_id | UUID | FK |
| vendor_id | UUID | FK |
| source_document_id | UUID | FK |
| captured_at | date | When the price is valid (from doc timestamp or ingest date) |
| price | numeric | Standard currency precision |
| currency | text | Defaults USD |
| quantity | integer | Available quantity |
| condition | text | e.g. `A/A-` from Abdursajid list |
| min_order_quantity | integer | Optional |
| location | text | Warehouse / region (e.g. `Miami Scan`) |
| notes | text | Free-form details (e.g. payment terms) |
| created_at | timestamptz | |

### price_history
Historically tracks price change snapshots per product/vendor combo.
| column | type | notes |
| --- | --- | --- |
| id | UUID | |
| product_id | UUID | |
| vendor_id | UUID | |
| price | numeric | |
| currency | text | |
| valid_from | timestamptz | Start timestamp |
| valid_to | timestamptz | Null indicates current price |
| source_offer_id | UUID | Points to originating offer |

### ingestion_jobs
Operational metadata for pipeline orchestration.
| column | type | notes |
| --- | --- | --- |
| id | UUID | |
| source_document_id | UUID | |
| processor | text | `excel_tabular`, `pdf_ocr`, `image_ocr`, `chat_parser`, etc. |
| status | text | `queued`/`running`/`succeeded`/`failed` |
| logs | jsonb | Error messages, metrics |
| created_at | timestamptz | |
| updated_at | timestamptz | |

## Key Normalization Rules
- Map raw description strings to canonical products via deterministic matching (SKU/UPC) first, then fuzzy/LLM matching.
- Preserve every raw row as JSON (`raw_payload`) attached to `offers` for full traceability.
- Use UTC timezone for timestamps; store document-local timezone in metadata if available.
- Capture currency explicitly—even if most price sheets use USD—to support future expansion.

## Immediate Parsing Considerations
- `Raw Data from Abdursajid.xlsx`: clean table with SKU, description, quantity, price, condition. Direct mapping to `offers`.
- `SB Technology Pricelist Oct 25.xlsx`: includes UPC, MPN, location (Warehouse). Add to `products` spec for stock location.
- `Laptop List Sept 10.xls`: requires header detection; first numeric column effectively `price`, preceding text is description. Need heuristics & fallback to LLM extraction.
- WhatsApp dumps & PDFs/images follow an OCR + prompt-based extraction flow producing structured offer rows before loading.

## Suggested Indexing
- `products`: unique index on `(brand, model_number)` and on `upc`.
- `offers`: composite index on `(product_id, vendor_id, captured_at)`.
- `price_history`: unique partial index `(product_id, vendor_id, valid_from)` to prevent duplicates.

