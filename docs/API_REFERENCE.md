# Pricebot API Reference

**REST API for querying vendor prices, product catalog, and price history.**

Base URL (local): `http://localhost:8000`  
Base URL (production): `https://your-app.up.railway.app`

---

## Authentication

**Current:** No authentication required (MVP)  
**Planned:** Bearer token authentication for production

---

## Endpoints Overview

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service metadata |
| `/health` | GET | Health check |
| `/offers` | GET | List recent offers with filters |
| `/products` | GET | List products with search |
| `/products/{id}` | GET | Get product details |
| `/vendors` | GET | List vendors with search |
| `/vendors/{id}` | GET | Get vendor details |
| `/price-history/product/{id}` | GET | Price history for product |
| `/price-history/vendor/{id}` | GET | Price history for vendor |
| `/documents` | GET | List ingested source documents |
| `/documents/{id}` | GET | Get document details |
| `/admin/documents` | GET | Operator UI dashboard (HTML) |

---

## 1. Service Info

### `GET /`

Returns service metadata.

**Response:**
```json
{
  "service": "Pricebot API",
  "environment": "local"
}
```

---

## 2. Health Check

### `GET /health`

Returns health status for monitoring.

**Response:**
```json
{
  "status": "healthy"
}
```

---

## 3. Offers

### `GET /offers`

List recent offers with optional filters.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 50 | Max results (1-500) |
| `product_id` | UUID | None | Filter by product |
| `vendor_id` | UUID | None | Filter by vendor |
| `since` | datetime | None | Filter by capture date (ISO 8601) |

**Example Request:**
```bash
curl "http://localhost:8000/offers?limit=10&vendor_id=f36e1066-7665-44ea-8ca9-70288f10f99a"
```

**Response:**
```json
[
  {
    "id": "abc123...",
    "product_id": "def456...",
    "vendor_id": "f36e1066...",
    "product_name": "iPhone 16 Pro 256GB",
    "vendor_name": "Tech Supplier",
    "price": 899.99,
    "currency": "USD",
    "captured_at": "2025-09-23T12:34:56",
    "condition": "New",
    "quantity": 50,
    "location": "Warehouse A"
  }
]
```

---

## 4. Products

### `GET /products`

List products with search and pagination.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | string | None | Search by name, model, or UPC |
| `limit` | int | 50 | Max results (1-500) |
| `offset` | int | 0 | Pagination offset |

**Example Request:**
```bash
curl "http://localhost:8000/products?q=iphone&limit=5"
```

**Response:**
```json
[
  {
    "id": "def456...",
    "canonical_name": "iPhone 16 Pro 256GB",
    "brand": "Apple",
    "model_number": "A2894",
    "upc": "194253715634",
    "category": "Smartphones",
    "offer_count": 12
  }
]
```

### `GET /products/{id}`

Get detailed product information with recent offers.

**Path Parameters:**
- `id` (UUID): Product ID

**Query Parameters:**
- `offer_limit` (int, default=20): Max recent offers to return

**Example Request:**
```bash
curl "http://localhost:8000/products/def456.../offers?offer_limit=5"
```

**Response:**
```json
{
  "id": "def456...",
  "canonical_name": "iPhone 16 Pro 256GB",
  "brand": "Apple",
  "model_number": "A2894",
  "upc": "194253715634",
  "category": "Smartphones",
  "offer_count": 12,
  "recent_offers": [
    {
      "id": "abc123...",
      "vendor_name": "Tech Supplier",
      "price": 899.99,
      "currency": "USD",
      "captured_at": "2025-09-23T12:34:56"
    }
  ]
}
```

---

## 5. Vendors

### `GET /vendors`

List vendors with search and pagination.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | string | None | Search by vendor name |
| `limit` | int | 50 | Max results (1-500) |
| `offset` | int | 0 | Pagination offset |

**Example Request:**
```bash
curl "http://localhost:8000/vendors?q=tech"
```

**Response:**
```json
[
  {
    "id": "f36e1066...",
    "name": "Tech Supplier",
    "offer_count": 145
  }
]
```

### `GET /vendors/{id}`

Get vendor details with recent offers.

**Path Parameters:**
- `id` (UUID): Vendor ID

**Query Parameters:**
- `offer_limit` (int, default=20): Max recent offers

**Example Request:**
```bash
curl "http://localhost:8000/vendors/f36e1066..."
```

**Response:**
```json
{
  "id": "f36e1066...",
  "name": "Tech Supplier",
  "offer_count": 145,
  "recent_offers": [
    {
      "id": "abc123...",
      "product_name": "iPhone 16 Pro 256GB",
      "price": 899.99,
      "currency": "USD",
      "captured_at": "2025-09-23T12:34:56"
    }
  ]
}
```

---

## 6. Price History

### `GET /price-history/product/{id}`

Get price history spans for a specific product across all vendors.

**Path Parameters:**
- `id` (UUID): Product ID

**Query Parameters:**
- `limit` (int, default=200): Max history entries

**Example Request:**
```bash
curl "http://localhost:8000/price-history/product/def456..."
```

**Response:**
```json
[
  {
    "id": "hist123...",
    "product_id": "def456...",
    "vendor_id": "f36e1066...",
    "price": 899.99,
    "currency": "USD",
    "valid_from": "2025-09-20T00:00:00",
    "valid_to": "2025-09-23T12:34:56",
    "source_offer_id": "abc123..."
  },
  {
    "id": "hist124...",
    "product_id": "def456...",
    "vendor_id": "f36e1066...",
    "price": 879.99,
    "currency": "USD",
    "valid_from": "2025-09-23T12:34:56",
    "valid_to": null,
    "source_offer_id": "abc124..."
  }
]
```

### `GET /price-history/vendor/{id}`

Get price history for all products from a specific vendor.

**Path Parameters:**
- `id` (UUID): Vendor ID

**Query Parameters:**
- `limit` (int, default=200): Max history entries

**Example Request:**
```bash
curl "http://localhost:8000/price-history/vendor/f36e1066..."
```

**Response:** Same format as product history endpoint.

---

## 7. Documents

### `GET /documents`

List ingested source documents.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 50 | Max results (1-500) |
| `offset` | int | 0 | Pagination offset |

**Example Request:**
```bash
curl "http://localhost:8000/documents?limit=10"
```

**Response:**
```json
[
  {
    "id": "doc123...",
    "file_name": "vendor_prices_oct.xlsx",
    "file_type": "spreadsheet",
    "status": "processed",
    "ingest_started_at": "2025-09-23T10:00:00",
    "ingest_completed_at": "2025-09-23T10:00:15",
    "offer_count": 145,
    "metadata": {
      "original_path": "/uploads/vendor_prices_oct.xlsx",
      "processor": "spreadsheet",
      "declared_vendor": "Tech Supplier"
    }
  }
]
```

### `GET /documents/{id}`

Get document details with extracted offers.

**Path Parameters:**
- `id` (UUID): Document ID

**Example Request:**
```bash
curl "http://localhost:8000/documents/doc123..."
```

**Response:**
```json
{
  "id": "doc123...",
  "file_name": "vendor_prices_oct.xlsx",
  "file_type": "spreadsheet",
  "status": "processed",
  "ingest_started_at": "2025-09-23T10:00:00",
  "ingest_completed_at": "2025-09-23T10:00:15",
  "offer_count": 145,
  "metadata": {
    "original_path": "/uploads/vendor_prices_oct.xlsx",
    "processor": "spreadsheet"
  },
  "offers": [
    {
      "id": "abc123...",
      "product_name": "iPhone 16 Pro 256GB",
      "vendor_name": "Tech Supplier",
      "price": 899.99,
      "currency": "USD",
      "captured_at": "2025-09-23T10:00:00"
    }
  ]
}
```

---

## Error Responses

All endpoints return standard HTTP status codes:

| Code | Meaning | Example |
|------|---------|---------|
| 200 | Success | Request completed |
| 400 | Bad Request | Invalid parameter format |
| 404 | Not Found | Resource doesn't exist |
| 422 | Validation Error | Pydantic validation failed |
| 500 | Internal Error | Server-side error |

**Error Response Format:**
```json
{
  "detail": "Product not found"
}
```

---

## Rate Limiting

**Current:** No rate limits (MVP)  
**Planned:** 1000 requests/hour per IP for production

---

## Pagination

For endpoints supporting pagination:

1. Use `limit` to set page size (max 500)
2. Use `offset` to skip results
3. Response headers include total count (planned)

**Example:**
```bash
# Page 1 (first 50)
curl "http://localhost:8000/products?limit=50&offset=0"

# Page 2 (next 50)
curl "http://localhost:8000/products?limit=50&offset=50"
```

---

## OpenAPI Specification

**Interactive Docs:** http://localhost:8000/docs  
**JSON Schema:** http://localhost:8000/openapi.json

Use the interactive Swagger UI to:
- Explore all endpoints
- Test requests in-browser
- View request/response schemas
- Generate client SDKs

---

## Client SDKs

Generate clients from OpenAPI spec:

```bash
# Install OpenAPI Generator
npm install -g @openapitools/openapi-generator-cli

# Generate Python client
openapi-generator-cli generate \
  -i http://localhost:8000/openapi.json \
  -g python \
  -o ./pricebot-client-python

# Generate TypeScript client
openapi-generator-cli generate \
  -i http://localhost:8000/openapi.json \
  -g typescript-fetch \
  -o ./pricebot-client-ts
```

---

## WebSocket Support

**Status:** Not implemented  
**Planned:** Real-time price updates via WebSocket in Phase 3

---

## Versioning

**Current:** No versioning (MVP)  
**Planned:** `/v1/` prefix for API stability

---

## Support

- **Issues:** https://github.com/itsAR-VR/pricebot/issues
- **Docs:** https://github.com/itsAR-VR/pricebot/tree/main/docs

---

**Last Updated:** September 30, 2025
