# MCP documentation — Research Platform

HTTP tool layer for LLM agents, served by the FastAPI backend (`backend/mcp.py`, an `APIRouter`).
This file is generated from the live catalogue `GET /.well-known/mcp-capabilities`
(25 capabilities; `market_sentiment`, `semantic_search` and `quant_taxonomy` are not detailed
below yet) — the paths below are the real routes.

## Access

* API: `http://127.0.0.1:8001` (loopback only). Every capability: `<METHOD> /mcp/<name>`.
* Run as macOS services: `scripts/launchd/install.sh` (api :8001, web :3001, ingest every
  30 min; `--remove` to uninstall; logs in `logs/`).
* Stdio MCP server for Claude Code: `Models-Macro/src/research_mcp/server.py` (server
  `research-platform`, tools `rp_*`), a thin client over these routes.
* Browser requests: CORS is limited to the local UI origins (`WEB_PORT`, default 3001, plus
  `RP_EXTRA_ORIGINS`); state-changing requests (POST/PATCH/DELETE) from any other `Origin` get
  403. Non-browser clients (no `Origin`) are unaffected.

## Conventions

* **Time**: every timestamp is UTC (`...Z`). Stored naive UTC (`backend/timeutil.py`).
* **`published_precision`**: `datetime` (real time of day), `date` (only the day is reliable),
  `seen` (undated item, first-seen time), `suspect` (feed date in the future: kept for search,
  never point-in-time evidence).
* **`available_at`**: earliest instant the paper is proven to exist — `datetime`: publication;
  `date`: end of that UTC day, or ingestion time if earlier; `seen`: ingestion; `suspect`: null.
  Point-in-time cuts (`start` / `end`) and citation checks use it.
* **Reference**: `ref = RP-` + first 8 hex chars of the paper UUID. `papers_resolve` / `cite`
  accept a ref, an 8-hex prefix or the UUID; an ambiguous prefix is an error.
* **Boolean query `q`**: `AND`, `OR`, `NOT`, parentheses, `"exact phrase"`, `-term`;
  juxtaposition = AND; terms match at word start, case-insensitive (title, abstract, summary,
  tags; full text where stored).
* **Zones**: USD EUR GBP JPY CHF CAD AUD NZD (+ CNY, GLB). A zone filter matches the source's
  zone or a zone the paper is about (`area`). Pairs: `EURUSD` or `EUR/USD`.
* **Full text**: `paper_fulltext` tries direct PDF, PDF linked from the landing page, HTML article
  body, then title + abstract (`text_source = pdf | html | abstract`), stores it (200k chars max).
  Outbound fetches are SSRF-guarded (http/https only, no private/loopback/link-local hosts, every
  redirect hop re-checked).
* **FX calls**: `bias` is INFERRED from cue words in title + abstract with FinBERT as fallback
  (`bias_inferred: true`); a single currency is mapped to its market pair (USD/JPY, EUR/USD) with
  the sign adjusted. It is not the desk's recommendation; no entry/stop levels (proprietary).
* **FinBERT** `sentiment_*`: tone of the text, not a trading signal.
* **Ingestion**: one pass at a time (file lock `.ingest.lock`); `ingest_trigger` returns
  `skipped_locked` when a pass is running; last pass summary in `.ingest_status.json`
  (`status`).
* **Macro facts** (`backend/macrofacts.py`, table `macro_facts`): numbers quoted in the articles
  (published prints, bank forecasts, consensus, previous prints, rate decisions). Each fact:
  `zone` (same codes as `fx_calls` / `area`), `geo` (statistical area inside the zone: `US`, `EA`,
  `DE`, `FR`, `ES`, `IT`, `UK`, `JP`, `JP-TKY` for Tokyo…), `indicator` (closed list
  `macrofacts.INDICATORS`), `kind` (`actual` `forecast` `consensus` `previous` `decision`), `value`,
  `unit` (`%` `bp` `k` `jobs` `index`), `period` (`YYYY`, `YYYY-MM`, `YYYY-Qn`, `YYYY-MM-DD`, null when
  the text gives none), `horizon` (forecasts), `quote` (exact excerpt ≤ 200 chars), `method`
  (`regex` | `llm`), `confidence`, `event_id` (ForexFactory event of the same zone, geo, indicator and
  UTC day; `/api/events` serves the same `id`). **Golden rule: a fact whose quote does not contain
  `value` as written is rejected** — no unit conversion (119,000 → `119000 jobs`, 119K → `119 k`), no
  sign added ("GDP drops 0.2%" is skipped). Computed at each ingestion pass for new papers; catch-up:
  `scripts/macro_facts.py --days N [--force] [--no-llm]`. LLM pass: LFM-3, at most
  `MF_MAX_LLM_PER_RUN` (default 15) papers with `impact_score ≥ MF_LLM_MIN_IMPACT` (60) where the
  rules found nothing; its JSON is schema-checked, then the golden rule, exact-excerpt and kind/quote
  checks apply. No price levels.
* **Events**: ForexFactory weekly JSON (this week + next week), 1 h memory cache, last good copy
  per file kept in `.events_cache.json` (the feed rate-limits with HTTP 429; next week appears
  late in the week). No hand-typed fallback calendar.

## Capabilities

### 1. `GET /mcp/papers_search`

Search stored research (central banks, desks, funds, multilateral, academic, news) with filters and an optional boolean query.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `horizon_days` | integer |  | Last N days (1-3650). |
| `start` | string |  | Window start, UTC (overrides horizon_days). |
| `end` | string |  | Window end, UTC (default now). Point-in-time cut. |
| `categories` | array<string> |  | Union of: central_bank, commercial_bank, fund, multilateral, academic, macro_news. |
| `zone` | string |  | USD EUR GBP JPY CHF CAD AUD NZD SEK INR GLB (source zone or paper area). |
| `sentiment` | string (positive / negative / neutral) |  |  |
| `pair` | string |  | EURUSD or EUR/USD. |
| `thematic_tags` | array<string> |  |  |
| `min_importance` | integer |  | 0-10: impact_score >= 10 x value. |
| `source_kind` | string (rss / scrape / article_scrape / portal) |  |  |
| `source` | string |  | Source name substring. |
| `q` | string |  | Boolean query: AND OR NOT ( ) "phrase", -term; juxtaposition = AND. |
| `sort` | string (date / impact) |  |  |
| `skip` | integer |  |  |
| `limit` | integer |  | <= 200 |

### 2. `GET /mcp/search_by_keywords`

Same as papers_search with a mandatory boolean query.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `horizon_days` | integer |  | Last N days (1-3650). |
| `start` | string |  | Window start, UTC (overrides horizon_days). |
| `end` | string |  | Window end, UTC (default now). Point-in-time cut. |
| `categories` | array<string> |  | Union of: central_bank, commercial_bank, fund, multilateral, academic, macro_news. |
| `zone` | string |  | USD EUR GBP JPY CHF CAD AUD NZD SEK INR GLB (source zone or paper area). |
| `sentiment` | string (positive / negative / neutral) |  |  |
| `pair` | string |  | EURUSD or EUR/USD. |
| `thematic_tags` | array<string> |  |  |
| `min_importance` | integer |  | 0-10: impact_score >= 10 x value. |
| `source_kind` | string (rss / scrape / article_scrape / portal) |  |  |
| `source` | string |  | Source name substring. |
| `q` | string | yes | Boolean query: AND OR NOT ( ) "phrase", -term; juxtaposition = AND. |
| `sort` | string (date / impact) |  |  |
| `skip` | integer |  |  |
| `limit` | integer |  | <= 200 |

### 3. `GET /mcp/papers_by_asset`

Papers about an asset class (keyword match).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `asset` | string (FX / Rates/Bonds / Equities / Commodities / Crypto / Volatility / Futures) | yes |  |
| `horizon_days` | integer |  | Last N days (1-3650). |
| `start` | string |  | Window start, UTC (overrides horizon_days). |
| `end` | string |  | Window end, UTC (default now). Point-in-time cut. |
| `categories` | array<string> |  | Union of: central_bank, commercial_bank, fund, multilateral, academic, macro_news. |
| `zone` | string |  | USD EUR GBP JPY CHF CAD AUD NZD SEK INR GLB (source zone or paper area). |
| `sentiment` | string (positive / negative / neutral) |  |  |
| `pair` | string |  | EURUSD or EUR/USD. |
| `thematic_tags` | array<string> |  |  |
| `min_importance` | integer |  | 0-10: impact_score >= 10 x value. |
| `source_kind` | string (rss / scrape / article_scrape / portal) |  |  |
| `source` | string |  | Source name substring. |
| `q` | string |  | Boolean query: AND OR NOT ( ) "phrase", -term; juxtaposition = AND. |
| `sort` | string (date / impact) |  |  |
| `skip` | integer |  |  |
| `limit` | integer |  | <= 200 |

### 4. `GET /mcp/papers_resolve`

Resolve citation refs (RP-xxxxxxxx, prefix or uuid) to papers.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `refs` | string | yes | Comma-separated refs. |

### 5. `GET /mcp/cite`

Frozen citation record of a paper (ref, institution, dates, url, excerpt) for a thesis or report.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `paper_id` | string | yes |  |

### 6. `GET /mcp/paper_fulltext`

Full text of a paper (PDF or article body), fetched once and stored.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `paper_id` | string | yes |  |
| `max_chars` | integer |  |  |
| `refresh` | boolean |  |  |

### 7. `GET /mcp/get_paper_by_id`

One paper with full metadata.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `paper_id` | string | yes |  |

### 8. `GET /mcp/window_digest`

Counts and top papers of a UTC window by zone and category (report collection).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `start` | string | yes | Window start, UTC (overrides horizon_days). |
| `end` | string |  | Window end, UTC (default now). Point-in-time cut. |
| `zones` | string |  |  |
| `per_zone` | integer |  |  |

### 9. `GET /mcp/platform_health`

Per-source freshness, document count and last ingestion error.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `detailed` | boolean |  |  |

### 10. `GET /mcp/get_stats`

Database counts.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `horizon_days` | integer |  | Last N days (1-3650). |

### 11. `GET /mcp/get_sources`

Provenance catalogue.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `detailed` | boolean |  |  |

### 12. `GET /mcp/get_central_banks`

Central-bank output by monetary zone.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `days` | integer |  |  |
| `per_zone` | integer |  |  |

### 13. `GET /mcp/get_by_institution`

Recent papers grouped by institution for a category.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `category` | string | yes |  |
| `days` | integer |  |  |
| `per` | integer |  |  |

### 14. `GET /mcp/fx_calls`

Directional FX stances of bank/fund notes (no levels).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `days` | integer |  |  |
| `pair` | string |  |  |
| `category` | string |  |  |

### 15. `GET /mcp/get_board`

Mean FinBERT sentiment by currency bloc.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `days` | integer |  |  |

### 16. `GET /mcp/get_events`

Upcoming high-impact events (ForexFactory).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `limit` | integer |  |  |
| `include_medium` | boolean |  |  |

### 17. `GET /mcp/quant_search`

arXiv q-fin search.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `q` | string |  |  |
| `asset` | string |  |  |
| `strategy` | string |  |  |
| `sort` | string |  |  |
| `limit` | integer |  |  |

### 18. `POST /mcp/get_digest`

Net sentiment over a list of paper ids.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `ids` | array<string> | yes |  |

### 19. `POST /mcp/link_thesis`

Tag a paper with a Horacle thesis id (backlink).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `paper_id` | string | yes |  |
| `thesis_id` | string | yes |  |

### 20. `POST /mcp/ingest_trigger`

Start one ingestion pass (no-op if one is running).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `analyze` | boolean |  |  |

### 21. `GET /mcp/macro_facts`

Numbers quoted in articles, each with its exact quote. Same body at `GET /api/macro/facts`
(which also returns the `indicators` dictionary).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `zone` | string |  | USD EUR GBP JPY CHF CAD AUD NZD CNY SEK NOK INR MXN |
| `indicator` | string |  | one of `macrofacts.INDICATORS` (422 otherwise) |
| `kind` | string |  | actual, forecast, consensus, previous, decision |
| `geo` | string |  | US EA DE FR ES IT NL BE UK JP JP-TKY CH CA AU NZ CN SE NO IN MX |
| `days` | integer |  | 1-730, default 30 (paper publication date) |
| `paper_id` | string |  |  |
| `limit` | integer |  | ≤ 2000, default 200 |

### 22. `GET /mcp/macro_board`

Per zone / geo / indicator / unit: `last_actual` (latest period; `confirmations`, `conflicts`),
`consensus` (same period), `surprise` = actual − consensus (same period and unit only),
`previous`, `decision`, `forecasts` per target period (latest forecast of each institution;
`median`, `dispersion` = max − min, `count`, `institutions`, quoted `values`). Median, dispersion and
surprise are the only computed numbers; the response repeats this in `method`. `coverage` lists the
indicator families present per zone. Same body at `GET /api/macro/board`.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `days` | integer |  | 1-365, default 14 |
| `zones` | string |  | comma-separated, e.g. `USD,EUR,GBP,JPY` |

## Legacy aliases

Old paths still answer (hidden from the schema): `/mcp/fx/calls` and `/mcp/get_transactions`
→ `fx_calls`. The previous documentation listed routes that did not exist; use the paths above.

## Citation workflow (Models-Macro)

1. find: `papers_search` / `window_digest` / `get_central_banks`;
2. read: `paper_fulltext` before quoting a number or a sentence;
3. cite: write `[RP-xxxxxxxx]` in the thesis or report text; `cite` returns the frozen card
   (title, institution, tier, published_date, precision, available_at, url, excerpt);
4. Models-Macro checks at save time that `available_at` ≤ the thesis creation (or report cut)
   and refuses anachronistic, unknown, ambiguous or `suspect` refs; `link_thesis` appends the
   thesis id to the paper's `thesis_tags`.
