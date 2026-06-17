"""Canonical ingestion: pull macro/financial research from institutional
RSS feeds + Elsevier/SSRN, deduplicate, auto-tag, then run FinBERT analysis
on everything published within the last week.

Run:  /opt/anaconda3/bin/python3 scripts/ingest.py [--no-analyze] [--days N]
"""
import sys
import os
import re
import argparse
import requests
import feedparser
from urllib.parse import urljoin
from dateutil import parser as date_parser
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.database import SessionLocal, engine, run_migrations  # noqa: E402
from backend.models import Paper, Base  # noqa: E402
from backend import analysis  # noqa: E402

_STOP = {"the", "and", "for", "from", "with", "this", "that", "into", "over",
         "evidence", "analysis", "model", "models", "using", "case", "study",
         "policy", "paper", "approach", "effects", "effect", "role", "data"}


def _sig(title: str) -> frozenset:
    """Significant-word signature for fuzzy cross-source dedup."""
    return frozenset(w for w in re.findall(r"[a-z0-9]+", (title or "").lower())
                     if len(w) > 3 and w not in _STOP)


def _is_fuzzy_dup(sig: frozenset, seen_sigs: list) -> bool:
    if len(sig) < 4:
        return False
    for s in seen_sigs:
        if not s:
            continue
        inter = len(sig & s)
        union = len(sig | s)
        if union and inter / union >= 0.82:   # Jaccard near-identical
            return True
    return False

# SSRN is distributed by Elsevier; this key drives the Elsevier/Scopus query.
ELSEVIER_API_KEY = os.environ.get("ELSEVIER_API_KEY", "f6a884c51312665bd93f3a3ea91e1f8c")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# The trader's arsenal: sell-side / bank research + central banks + academic.
# All URLs verified to return live RSS entries.
SOURCES = {
    # --- Sell-side / bank research desks ---
    "ING THINK": "https://think.ing.com/rss/",
    # --- Central banks ---
    "ECB Research": "https://www.ecb.europa.eu/rss/pub.html",
    "ECB Blog": "https://www.ecb.europa.eu/rss/blog.html",
    "ECB Press": "https://www.ecb.europa.eu/rss/press.html",
    "Fed Board (FEDS)": "https://www.federalreserve.gov/feeds/feds.xml",
    "Fed Board (IFDP)": "https://www.federalreserve.gov/feeds/ifdp.xml",
    "Fed Press": "https://www.federalreserve.gov/feeds/press_all.xml",
    "Fed Speeches": "https://www.federalreserve.gov/feeds/speeches.xml",
    "Fed NY Liberty St": "https://libertystreeteconomics.newyorkfed.org/feed/",
    "Fed Atlanta Macroblog": "https://www.atlantafed.org/rss/macroblog",
    "Bank of England": "https://www.bankofengland.co.uk/rss/publications",
    "Bank of England News": "https://www.bankofengland.co.uk/rss/news",
    "Bank of Japan": "https://www.boj.or.jp/en/rss/whatsnew.xml",
    "Bank of Canada": "https://www.bankofcanada.ca/feed/",
    # --- Multilateral / BIS / IMF ---
    "BIS Hub": "https://www.bis.org/doclist/reshub_papers.rss",
    "BIS WP": "https://www.bis.org/doclist/bis_fsi_publs.rss",
    "IMF Working Papers": "https://www.imf.org/en/Publications/RSS?language=eng&series=IMF%20Working%20Papers",
    # --- Academic / aggregators ---
    "NBER": "https://www.nber.org/rss/new.xml",
    "Macro (NEP)": "https://nep.repec.org/rss/nep-mac.rss.xml",
    "Finance (NEP)": "https://nep.repec.org/rss/nep-fin.rss.xml",
    "Monetary (NEP)": "https://nep.repec.org/rss/nep-mon.rss.xml",
    "Banking (NEP)": "https://nep.repec.org/rss/nep-ban.rss.xml",
}

SCOPUS_URL = "https://api.elsevier.com/content/search/scopus"

THEMATIC_KEYWORDS = {
    "Monetary Policy": ["monetary policy", "interest rate", "central bank", "inflation target", "quantitative easing", "rate hike", "rate cut"],
    "Liquidity": ["liquidity", "reserve", "money market", "repo", "funding"],
    "Financial Stability": ["financial stability", "systemic risk", "macroprudential", "banking", "basel", "stress test", "insolvency", "default"],
    "Inflation": ["inflation", "price index", "cpi", "pce", "deflation", "disinflation"],
    "Labor Market": ["labor", "labour", "employment", "unemployment", "wages", "productivity"],
    "Fiscal Policy": ["fiscal", "debt", "tax", "government spending", "deficit", "sovereign"],
    "Digital Currency": ["cbdc", "crypto", "digital currency", "bitcoin", "blockchain", "stablecoin", "tokenization"],
    "Macro-Finance": ["term premium", "risk premium", "asset pricing", "credit cycle", "yield curve", "equity"],
}

COUNTRY_KEYWORDS = {
    "US": ["united states", "fed ", "federal reserve", " u.s.", "american", "new york fed", "treasury"],
    "EU": ["euro", "ecb", "european", "eurozone", "germany", "france", "italy", "spain"],
    "UK": ["uk ", "u.k.", "united kingdom", "bank of england", "boe", "gilt", "sterling"],
    "China": ["china", "chinese", "pboc", "renminbi", "shanghai", "yuan"],
    "Japan": ["japan", "boj", "yen", "tokyo"],
    "Global": ["global", "international", "world", "emerging markets", "cross-border"],
}


def normalize_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def guess_tags(text: str, tag_dict: dict) -> list:
    text = (text or "").lower()
    return [tag for tag, kws in tag_dict.items() if any(kw in text for kw in kws)]


def _parse_date(entry):
    """Prefer feedparser's parsed struct_time fields, fall back to strings.
    Reject implausible/future dates (bad feeds) by trying the other field."""
    now = datetime.now()
    candidates = []
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            try:
                candidates.append(datetime(*st[:6]))
            except Exception:
                pass
    for key in ("published", "updated", "dc_date", "date"):
        s = entry.get(key)
        if s:
            try:
                d = date_parser.parse(s)
                candidates.append(d.replace(tzinfo=None) if d.tzinfo else d)
            except Exception:
                pass
    # earliest non-future candidate is the most trustworthy
    valid = [d for d in candidates if d <= now]
    if valid:
        return max(valid)
    return now  # nothing usable / all future -> treat as just published


def add_paper(db, seen, seen_sigs, *, title, source, abstract="", authors=None,
              published_date=None, source_url=None, pdf_url=None, doi=None):
    """Dedup (exact hash + DOI + fuzzy title) then insert with tags + FX pairs.
    Returns True if a new row was added."""
    title = (title or "Untitled").strip()
    clean_hash = normalize_title(title)
    if not clean_hash or clean_hash in seen:
        return False
    if db.query(Paper).filter(Paper.clean_title_hash == clean_hash).first():
        return False
    if doi and db.query(Paper).filter(Paper.doi == doi).first():
        return False
    sig = _sig(title)
    if _is_fuzzy_dup(sig, seen_sigs):
        return False

    abstract = re.sub(r"<[^>]+>", "", abstract or "").strip()
    blob = f"{title} {abstract}"
    paper = Paper(
        title=title, authors=authors or source,
        abstract=abstract, published_date=published_date or datetime.now(),
        source=source,
        country_tags=guess_tags(blob, COUNTRY_KEYWORDS) or ["Global"],
        thematic_tags=guess_tags(blob, THEMATIC_KEYWORDS),
        pdf_url=pdf_url, source_url=source_url,
        doi=doi, clean_title_hash=clean_hash, created_at=datetime.utcnow(),
    )
    paper.currency_pairs = analysis.derive_currency_pairs(paper)
    db.add(paper)
    seen.add(clean_hash)
    seen_sigs.append(sig)
    return True


def ingest_rss(db, url, source_name, seen, seen_sigs):
    print(f"[*] {source_name}...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        print(f"    [!] feed error: {e}")
        return 0

    added = 0
    for entry in feed.entries:
        source_url = entry.get("link")
        pdf_url = None
        for link in entry.get("links", []):
            href = link.get("href", "")
            if link.get("type") == "application/pdf" or href.endswith(".pdf"):
                pdf_url = href
                break
        if not pdf_url and source_url and source_url.endswith(".pdf"):
            pdf_url = source_url

        if add_paper(db, seen, seen_sigs,
                     title=entry.get("title", "Untitled"),
                     authors=entry.get("author", source_name),
                     abstract=entry.get("summary", entry.get("description", "")),
                     published_date=_parse_date(entry), source=source_name,
                     source_url=source_url, pdf_url=pdf_url,
                     doi=entry.get("prism_doi")):
            added += 1
    db.commit()
    print(f"    + {added} new")
    return added


def scrape_html(db, url, source_name, seen, seen_sigs, *, href_contains, base, min_title=30):
    """Lightweight scraper for institutions with no usable RSS (Bundesbank,
    Banque de France, Deutsche Bank). Grabs publication anchors by href pattern."""
    print(f"[*] {source_name} (scrape)...")
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
    except Exception as e:
        print(f"    [!] scrape error: {e}")
        return 0

    added = 0
    seen_local = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if href_contains not in href.lower() or len(text) < min_title:
            continue
        if href in seen_local:
            continue
        seen_local.add(href)
        if add_paper(db, seen, seen_sigs, title=text, source=source_name,
                     source_url=urljoin(base, href)):
            added += 1
    db.commit()
    print(f"    + {added} new")
    return added


def ingest_scopus(db, seen, seen_sigs):
    print("[*] Elsevier / SSRN (Scopus)...")
    headers = {"X-ELS-APIKey": ELSEVIER_API_KEY, "Accept": "application/json"}
    params = {
        "query": "TITLE-ABS-KEY(macroeconomics OR monetary OR finance) AND PUBYEAR > 2024",
        "count": 25, "sort": "-coverDate",
    }
    try:
        resp = requests.get(SCOPUS_URL, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        entries = resp.json().get("search-results", {}).get("entry", [])
    except Exception as e:
        print(f"    [!] Scopus error: {e}")
        return 0

    added = 0
    for entry in entries:
        authors = entry.get("author", [])
        if isinstance(authors, list):
            names = ", ".join(a.get("authname", "") for a in authors[:3]) or "SSRN Author"
        else:
            names = entry.get("dc:creator", "SSRN Author")
        try:
            pub = date_parser.parse(entry.get("prism:coverDate"))
        except Exception:
            pub = datetime.now()
        doi = entry.get("prism:doi")
        if add_paper(db, seen, seen_sigs,
                     title=entry.get("dc:title", "Untitled"), authors=names,
                     abstract=entry.get("dc:description", "Click link for abstract."),
                     published_date=pub, source="Elsevier/SSRN",
                     source_url=f"https://doi.org/{doi}" if doi else None, doi=doi):
            added += 1
    db.commit()
    print(f"    + {added} new")
    return added


# Institutions with no usable RSS -> scraped from their listing pages.
SCRAPE_SOURCES = [
    {"source_name": "Bundesbank", "url": "https://www.bundesbank.de/en/publications/research/discussion-papers",
     "href_contains": "/discussion-papers/", "base": "https://www.bundesbank.de"},
    {"source_name": "Banque de France", "url": "https://www.banque-france.fr/en/publications-and-statistics/publications/working-papers",
     "href_contains": "/working-paper", "base": "https://www.banque-france.fr"},
    {"source_name": "Deutsche Bank Research", "url": "https://www.dbresearch.com/PROD/RPS_EN-PROD/PROD0000000000515356/Research.xhtml",
     "href_contains": "prod000", "base": "https://www.dbresearch.com"},
]


def run(do_analyze=True, days=7):
    Base.metadata.create_all(bind=engine)
    run_migrations()
    db = SessionLocal()
    seen = set()
    # preload existing title signatures so fuzzy dedup spans the whole DB
    seen_sigs = [_sig(t) for (t,) in db.query(Paper.title).all()]
    total = 0
    try:
        total += ingest_scopus(db, seen, seen_sigs)
        for name, url in SOURCES.items():
            total += ingest_rss(db, url, name, seen, seen_sigs)
        for cfg in SCRAPE_SOURCES:
            total += scrape_html(db, cfg["url"], cfg["source_name"], seen, seen_sigs,
                                 href_contains=cfg["href_contains"], base=cfg["base"])
        print(f"\n[=] ingestion complete: {total} new papers\n")

        if do_analyze:
            print(f"[*] running FinBERT analysis on papers <= {days} days old...")
            n = analysis.analyze_recent(db, days=days)
            print(f"[=] analyzed {n} papers")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-analyze", action="store_true", help="skip FinBERT analysis")
    ap.add_argument("--days", type=int, default=7, help="analysis lookback window")
    args = ap.parse_args()
    print(f"[{datetime.now()}] SYNC START")
    run(do_analyze=not args.no_analyze, days=args.days)
    print(f"[{datetime.now()}] SYNC DONE")
