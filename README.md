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

## Sources (the arsenal)
Sell-side: **ING THINK**. Central banks: **ECB** (research/blog/press), **Fed**
(FEDS, IFDP, press, speeches, NY Liberty St, Atlanta macroblog), **Bank of
England** (publications + news), **Bank of Japan**, **Bank of Canada**.
Multilateral: **BIS**, **IMF**. Academic: **NBER**, **RePEc/NEP**,
**Elsevier/SSRN**. Add more in `scripts/ingest.py` → `SOURCES`.

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
| `GET /api/papers` | filters: `search`, `horizon_days`, `thematic_tags`, `country_tags`, `source`, `sentiment` |
| `GET /api/papers/{id}` | single paper |
| `GET /api/papers/{id}/report` | generate PDF compte rendu (auto-analyzes if needed) |
| `GET /api/papers/{id}/download` | proxy the source PDF |
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

Three tabs keep it clean:
- **FEED** — cards with sentiment, interest stars, FX-pair chips, NEW badges,
  compte rendu, filters incl. **currency pair** (EUR/USD, USD/JPY, DXY…),
  saved-only view, sort (interest/date/score/conviction), CSV export, AUTO refresh.
- **CALENDAR** — upcoming central-bank meetings + key releases (FOMC, ECB, BoE,
  BoJ, CPI, NFP) with countdowns and "N related" links into the feed.
- **DIGEST** — net FinBERT bias across your saved watchlist, by bloc + top movers.

`"NEW" badges` use a localStorage last-visit stamp vs each paper's `created_at`.

## Sourcing internals
- **Scrapers** for no-RSS institutions (Bundesbank works; Banque de France /
  Deutsche Bank are JS-gated, best-effort) — `SCRAPE_SOURCES` in `scripts/ingest.py`.
- **Cross-source de-dup**: exact title hash + DOI + fuzzy (Jaccard ≥ 0.82 on
  significant words) so the same paper from NEP/SSRN/publisher lands once.
- **Full-text sentiment**: direct PDF → PDF linked from the landing page →
  abstract, in that order.
- **Bad-date guard**: future-dated feed rows are hidden everywhere.
