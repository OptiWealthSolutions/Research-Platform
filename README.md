# Macro Research Terminal

Aggregates macro/financial research from central banks, institutions and
sell-side / retail sources (BIS, ECB, Fed, Bank of England, IMF, NBER, RePEc/NEP,
Elsevier/SSRN), deduplicates and auto-tags it, then runs a **FinBERT** analysis
layer (sentiment + key terms + summary) on every paper published in the last
week — built for a retail trader's workflow.

## Stack
- **Backend**: FastAPI + SQLAlchemy + SQLite
- **Analysis**: FinBERT (`ProsusAI/finbert`) sentiment, RAKE-style keyword
  extraction, extractive summarization, `fpdf2` PDF "compte rendu", `pypdf`
  full-text extraction
- **Frontend**: static HTML + vanilla JS + Tailwind (CDN) — terminal theme
- **Interpreter**: `/opt/anaconda3/bin/python3` (has all ML deps installed)

## Run
```bash
./start.sh          # clean-starts backend (:8001) + frontend (:3001) + auto-ingest scheduler
./kill.sh           # stops all three
SCHED=0 ./start.sh  # start without the scheduler
```
- Frontend: http://localhost:3001
- API docs: http://localhost:8001/docs

Env overrides: `PYBIN`, `API_PORT`, `WEB_PORT`, `INGEST_INTERVAL` (scheduler seconds, default 10800 = 3h).

## Daily watchlist digest (optional email)
The Save (☆) button mirrors your watchlist to `watchlist.json`. Email a daily
net-sentiment digest of it:
```bash
export SMTP_HOST=smtp.gmail.com SMTP_PORT=587 \
       SMTP_USER=you@gmail.com SMTP_PASS=app_password DIGEST_TO=you@gmail.com
# cron: 08:00 daily
0 8 * * * /opt/anaconda3/bin/python3 /path/scripts/digest.py >> digest_log.txt 2>&1
```
Without SMTP it just prints the digest. The app's DIGEST tab shows it live.

## Sources (the arsenal) — `backend/sources.py`
The whole provenance map lives in **one catalog** (`backend/sources.py` →
`CATALOG`). Every entry records where a paper really comes from: its upstream
`feed`/`portal`, its `kind` (`rss` auto-ingested · `scrape` best-effort ·
`portal` JS/login-gated, linked but not pulled), its `category` and its
**monetary zone**. The ingester reads the `rss`/`scrape` rows; the API serves
the catalog so the UI can show exactly where each doc originates (the **DESKS**
tab) — the same transparency model as a desk terminal's bank-research feed.

- **Central banks** (by zone): **USD** Fed (FEDS/IFDP/press/speeches/NY Liberty
  St/Atlanta) · **EUR** ECB (research/blog/press), Bundesbank, Banque de France ·
  **GBP** Bank of England (pubs + news) · **JPY** Bank of Japan · **CHF** SNB
  (press + speeches) · **CAD** Bank of Canada · **SEK** Riksbank (press +
  speeches) · **INR** RBI.
- **Multilateral**: BIS (hub/WP), IMF.
- **Commercial banks**: **ING** + **Wells Fargo** (live RSS); **Goldman Sachs**,
  **J.P. Morgan**, **HSBC** (static scrape); **Danske Bank** (headless scrape).
  Portals (login-walled / no public feed, linked for navigation): SEB, MUFG,
  Westpac, Nordea, Natixis, Scotiabank, KBC, UniCredit, CIBC, SocGen, StanChart,
  Deutsche Bank, Barclays, BNP Paribas, Morgan Stanley, Citi, RBC, TD, Crédit
  Agricole, Nomura, Commerzbank, BBVA, Intesa, Lloyds, NatWest, ANZ, NAB, Macquarie.
- **Funds & asset managers**: **Robeco**, **Man Group**, **Amundi** (static
  scrape); **Schroders** (headless scrape). Portals: BlackRock, PIMCO, Invesco,
  Vanguard, Fidelity, T. Rowe Price, Janus Henderson, Franklin Templeton,
  AllianceBernstein, State Street, Capital Group, AQR.
- **Academic**: NBER, RePEc/NEP (mac/fin/mon/ban), Elsevier/SSRN.

Article scrapers (`backend/scrapers.py`) parse insight listings — each catalog
config says how to find article links and where the date lives (parent container ·
glued into the link text · URL path). Static pages use `requests`; client-rendered
public pages set `render: true` and go through **headless Chromium (Playwright)**.
Login-walled desks (SEB/MUFG/Danske *research* portals…) are never bypassed — they
stay `portal`. Scraped articles land in the same `papers` table (dedup +
sentiment), tagged `commercial_bank` / `fund`. `pip install playwright &&
playwright install chromium` for the headless scrapers.

Add a source by appending one `_s(...)` row to `CATALOG` — ingest, filters and
both new tabs pick it up automatically.

## Ingest + analyze
```bash
# Pull all feeds + Elsevier/SSRN, then FinBERT-analyze papers <= 7 days old
/opt/anaconda3/bin/python3 scripts/ingest.py

# Analyze only (no fetch)
/opt/anaconda3/bin/python3 scripts/analyze.py --days 7      # recent
/opt/anaconda3/bin/python3 scripts/analyze.py --all         # whole DB

# Hourly auto-sync loop
/opt/anaconda3/bin/python3 automate_platform.py
```
SSRN/Elsevier key: set `ELSEVIER_API_KEY` env var (falls back to the bundled key).

## API
| Endpoint | Purpose |
|---|---|
| `GET /api/papers` | filters: `search`, `horizon_days`, `thematic_tags`, `country_tags`, `source`, `category`, `zone`, `sentiment`, `pair` |
| `GET /api/sources` | provenance map: every desk/institution, its kind/category/zone/portal + doc count |
| `GET /api/central-banks?days=N` | recent central-bank output grouped by monetary zone (press · speeches · pubs) + net bias |
| `GET /api/by-institution?category=commercial_bank\|fund&days=N` | recent articles grouped by institution, sorted by date + net bias (Banks / Funds tabs) |
| `GET /api/transactions?days=N&category=&pair=` | published FX calls (institution · pair · directional bias · thesis) extracted from bank/fund research — no fabricated entry/TP/SL |
| `GET /api/papers/{id}` | single paper |
| `GET /api/papers/{id}/report` | generate PDF compte rendu (auto-analyzes if needed) |
| `GET /api/papers/{id}/download` | proxy the source PDF |
| `GET /api/papers/{id}/pdfinfo` | resolve (scrape + cache) whether a full-article PDF exists |
| `GET /api/papers/{id}/pdf` | proxy the bank's full-article PDF (ING, Danske… expose one) |
| `POST /api/papers/{id}/analyze` | run FinBERT on one paper |
| `POST /api/analyze?days=N` | background-analyze recent papers |
| `GET /api/stats` | totals + sentiment distribution |

## Frontend — trader cockpit (Bloomberg-style)
- **Header**: live UTC clock + FX session badges (SYD/TOK/LDN/NYC open/closed).
- **Command toolbar**: search + horizon/source/theme/region/sentiment filters,
  `FINBERT` (analyze recent) and `SYNC` (refresh) buttons, live doc/score stats.
- **Macro Sentiment Board**: net FinBERT bias per currency bloc
  (USD/EUR/GBP/JPY/CNY/GLB) with signed score, ▲/▼ arrow, doc count and a
  pos/neu/neg bar. Click a bloc to filter the feed.
- **Dense research table**: DATE · SRC · REG · SENT · SCORE · THEMES · RESEARCH,
  sortable by any column. Click a row to expand inline: compte rendu, abstract,
  FinBERT distribution, key terms, tags, and REPORT PDF / SOURCE PDF / ARTICLE
  actions.
- **Keyboard**: `/` search · `j`/`k` or ↑/↓ move cursor · `Enter`/`o` expand ·
  `r` sync · `Esc` blur.

Five tabs keep it clean:
- **FEED** — master-detail: compact article **list on the left**, **preview on
  the right**. Clicking a row shows that article's compte rendu + abstract +
  FinBERT analysis + actions; when no text preview is available it falls back to
  the auto-generated **FinBERT report PDF** embedded inline. Filters incl. **type**
  (central bank / commercial bank / fund / multilateral / academic) and **currency
  pair**, saved-only, sort, CSV export, AUTO refresh.
- **TRANSACTIONS** — published **FX calls** pulled from bank/fund research:
  institution · date · pair · directional bias · thesis, sorted by date. Bias is
  read from the note (cues + FinBERT). Broker entry/TP/SL levels are a licensed
  trade-rec product — not shown, never fabricated.
- **CENTRAL BANKS** — recent central-bank output laid out **by monetary zone**
  (USD/EUR/GBP/JPY/CHF/CAD/SEK/INR/GLB): each zone card shows the authority, net
  FinBERT bias and the latest press releases · speeches · publications. Click a
  zone header to drill into the feed.
- **BANKS** — commercial banks as **columns, one per institution**, articles
  sorted by date. Live-RSS/scraped desks (ING, Wells Fargo, Goldman, J.P. Morgan,
  HSBC…) show their articles + net bias; the rest appear as portal cards linking
  straight to the desk. Every tracked bank is visible.
- **FUNDS** — same column layout for asset managers (Robeco, Man Group, Amundi
  scraped; BlackRock, PIMCO, Vanguard… as portals).
- **DESKS** — the **source map**: every desk/institution we aggregate, grouped by
  category, with its zone, domain, LIVE-RSS/SCRAPED/PORTAL kind, doc count and a
  direct link to its portal. Answers "where does each paper come from".
- **CALENDAR** — upcoming central-bank meetings + key releases (FOMC, ECB, BoE,
  BoJ, CPI, NFP) with countdowns and "N related" links into the feed.
- **DIGEST** — net FinBERT bias across your saved watchlist, by bloc + top movers.

`"NEW" badges` use a localStorage last-visit stamp vs each paper's `created_at`.

## Sourcing internals
- **One catalog** (`backend/sources.py`) is the single source of truth: ingest
  feeds, the `category`/`zone` filters, the Central Banks zones and the Desks map
  all derive from it.
- **Scrapers** for no-RSS institutions: static (`requests`) for Bundesbank and
  the bank/fund insight pages that render server-side; **headless Chromium
  (Playwright)** for client-rendered public pages (`render: true`). Never used to
  cross a login wall.
- **Cross-source de-dup**: exact title hash + DOI + fuzzy (Jaccard ≥ 0.82 on
  significant words) so the same paper from NEP/SSRN/publisher lands once.
- **Full-text sentiment**: direct PDF → PDF linked from the landing page →
  abstract, in that order.
- **Bad-date guard**: future-dated feed rows are hidden everywhere.
