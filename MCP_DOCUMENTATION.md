# MCP (Model-Context-Protocol) Documentation for LLMs

## Overview
The Model-Context-Protocol (MCP) provides a standardized interface for LLMs to interact with the Macro Research Terminal platform. It exposes 16 capabilities for searching, filtering, and analyzing macroeconomic research papers, FX calls, platform health, and more.

## Base URL
```
http://localhost:8001/mcp
```

## Discovery Endpoint
```
GET /.well-known/mcp-capabilities
```

Returns a JSON dictionary of 16 available capabilities with their parameter schemas and response descriptions.

## Available Capabilities

### 1. papers_search
Search for research papers with advanced filtering.

**Parameters:**
- `horizon_days` (integer, optional): Only papers published in the last N days (1-365)
- `categories` (array of strings, optional): Paper categories: central_bank, commercial_bank, fund, multilateral, academic, macro_news
- `zone` (string, optional): Monetary zone: USD, EUR, GBP, JPY, CHF, CAD, SEK, INR, GLB
- `sentiment` (string, optional): Sentiment label: positive, negative, neutral
- `pair` (string, optional): Currency pair filter (e.g. EURUSD, GBPUSD)
- `thematic_tags` (array of strings, optional): Thematic tags to match
- `min_importance` (integer, optional): Minimum FinBERT importance score (0-10)
- `source_kind` (string, optional): Kind of source: rss, scrape, article_scrape, portal
- `skip` (integer, default: 0): Pagination offset
- `limit` (integer, default: 50, max: 200): Results limit

**Response:** List of papers with id, title, source, published_date, sentiment_score, source_url, pdf_url

### 2. papers_by_asset
Get papers filtered by asset class.

**Parameters:**
- `asset` (string, required): Equities | FX | Crypto | Commodities | Rates/Bonds | Volatility | Futures
- `horizon_days` (integer, optional): Only papers published in the last N days
- `limit` (integer, default: 50, max: 200)

**Response:** Papers tagged with the specified asset class

### 3. platform_health
Check the health of the platform's sources.

**Parameters:**
- `detailed` (boolean, default: true): Return per-source details

**Response:** Overall status (healthy/degraded/error) + list of sources with last_published, days_since, status, total_docs, last_error

### 4. quant_search
Search arXiv quantitative finance papers.

**Parameters:**
- `q` (string, default: ""): Free-text query
- `asset` (string, optional): Equities, FX, Crypto, Commodities, Rates/Bonds, Volatility, Futures
- `strategy` (string, optional): Momentum, Mean-Reversion, Stat-Arb / Pairs, Market-Making, Factor, Machine-Learning, Reinforcement-Learning, Execution, Portfolio-Opt, High-Frequency
- `sort` (string, default: "submittedDate"): submittedDate or relevance
- `limit` (integer, default: 40, max: 100)

**Response:** arXiv papers with title, authors, abstract, pdf_url, primary category, detected assets/methods

### 5. fx_calls
Extract published FX calls from bank/fund research.

**Parameters:**
- `days` (integer, default: 120): Look back window in days (1-730)
- `pair` (string, optional): Filter by currency pair (e.g. EURUSD)
- `category` (string, default: "commercial_bank"): commercial_bank or fund

**Response:** FX calls with institution, pair, bias (bullish/bearish/neutral), thesis, and source URL

### 6. ingest_trigger
Trigger a manual ingestion run (RSS + scraping + optional FinBERT analysis).

**Parameters:**
- `analyze` (boolean, default: true): Also run FinBERT analysis on recent papers

**Response:** Job status: "started" with timestamp

### 7. get_stats
Get statistics about the database.

**Parameters:**
- `horizon_days` (integer, optional): Lookback window in days

**Response:** StatsResponse with total, analyzed, sentiment distribution, sources breakdown, last_week count

### 8. get_sources
Get the full provenance map - every desk/institution with metadata.

**Parameters:**
- `detailed` (boolean, default: true): Include feed/scrape config details

**Response:** SourceCatalogResponse with categories, zones, and 112 SourceItem objects with name, institution, domain, kind, category, zone, portal, feed, count, last_published, days_since_last, last_error, status

### 9. get_central_banks
Get recent central-bank output grouped by monetary zone.

**Parameters:**
- `days` (integer, default: 30): Lookback window (1-365)
- `per_zone` (integer, default: 6): Max papers per zone

**Response:** CentralBankResponse with zones and latest papers per zone with sentiment scores

### 10. get_by_institution
Get recent articles grouped by institution.

**Parameters:**
- `category` (string, required): commercial_bank | fund | central_bank | multilateral | academic | macro_news
- `days` (integer, default: 180): Lookback window (1-730)
- `per` (integer, default: 8): Max papers per institution

**Response:** ByInstitutionResponse with institution groups sorted by count, each with latest papers

### 11. get_transactions
Extract published FX calls from bank/fund research.

**Parameters:**
- `days` (integer, default: 120): Lookback window (1-730)
- `pair` (string, optional): Filter by currency pair (e.g. EURUSD)
- `category` (string, default: "commercial_bank"): commercial_bank or fund

**Response:** TransactionsResponse with FX calls list

### 12. get_board
Get net FinBERT sentiment per currency bloc (cockpit strip).

**Parameters:**
- `days` (integer, default: 7): Window in days (1-365)

**Response:** BoardResponse with BlocSentiment for USD, EUR, GBP, JPY, CNY, GLB

### 13. get_digest
Get net sentiment summary over a set of paper IDs (user's watchlist).

**Parameters:**
- `ids` (array of strings, required): List of paper IDs

**Response:** DigestResponse with net_score, net_label (bullish/neutral/bearish), positive/negative/neutral counts, by_bloc breakdown, top_bullish/top_bearish papers

### 14. get_paper_by_id
Get a single paper by its UUID.

**Parameters:**
- `paper_id` (string, required): Paper ID (UUID format)

**Response:** Full paper metadata including id, title, authors, abstract, published_date, source, institution, zone, category, sentiment_label, sentiment_score, source_url, pdf_url, currency_pairs, thematic_tags, created_at, analyzed_at, thesis, thesis_tags

### 15. get_events
Get the macro event calendar (FOMC, CPI, NFP, etc.).

**Parameters:**
- `limit` (integer, default: 12): Max events to return

**Response:** List of MacroEvent with date, days_until, name, bloc, region, importance (high/medium), theme, related_count

### 16. search_by_keywords
Advanced search by combined keywords with boolean logic (AND/OR/NOT) and filters.

**Parameters:**
- `q` (string, required): Boolean search query (AND/OR/NOT, phrases in quotes)
- `horizon_days` (integer, optional): Only papers published in the last N days
- `categories` (array of strings, optional): Paper categories
- `zone` (string, optional): Monetary zone filter
- `sentiment` (string, optional): Sentiment label
- `pair` (string, optional): Currency pair filter
- `limit` (integer, default: 50, max: 200)

**Response:** List of papers matching the boolean query with filters

## Example Usage

### Check Platform Health
```bash
curl "http://localhost:8001/mcp/platform_health?detailed=false"
```

### Get Statistics
```bash
curl "http://localhost:8001/mcp/get_stats?horizon_days=30"
```

### Search Papers by Category and Zone
```bash
curl "http://localhost:8001/mcp/papers_search?horizon_days=7&categories=central_bank&zone=USD"
```

### Search by Asset Class
```bash
curl "http://localhost:8001/mcp/papers_by_asset?asset=FX&horizon_days=7"
```

### Extract FX Calls
```bash
curl "http://localhost:8001/mcp/fx_calls?days=30&category=commercial_bank"
```

### Trigger Ingestion
```bash
curl -X POST "http://localhost:8001/mcp/ingest_trigger?analyze=true"
```

### Get Digest for Watchlist
```bash
curl -X POST "http://localhost:8001/mcp/get_digest" \
  -H "Content-Type: application/json" \
  -d '{"ids": ["paper-id-1", "paper-id-2"]}'
```

### Search with Boolean Query
```bash
curl "http://localhost:8001/mcp/search_by_keywords?q=monetary%20policy&horizon_days=7&limit=5"
```

### Get arXiv Quant Search
```bash
curl "http://localhost:8001/mcp/quant_search?q=momentum&asset=FX&strategy=Momentum&limit=5"
```

## MCP Capability Discovery

```bash
curl http://localhost:8001/.well-known/mcp-capabilities
```

Returns JSON with all 16 capability definitions including parameter schemas and response descriptions.

## Integration with LLMs

The MCP is designed for LLM consumption:
- All parameters are optional except where noted (`required` field)
- Responses are consistent JSON shapes
- Discovery endpoint allows LLMs to introspect available capabilities
- Parameter validation can be done against the capability definitions
- Error responses follow FastAPI/HTTP standards

## Platform Status

The platform is running via `start.sh` which launches:
- Backend API on port 8001 (FastAPI + uvicorn)
- Frontend static server on port 3001
- Auto-ingest scheduler (every 30 minutes)

Kill the server with: `./kill.sh`