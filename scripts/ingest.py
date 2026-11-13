"""Canonical ingestion: pull macro/financial research from institutional
RSS feeds + Elsevier/SSRN, deduplicate, auto-tag, then run FinBERT analysis
on everything published within the last week.

Run:  /opt/anaconda3/bin/python3 scripts/ingest.py [--no-analyze] [--days N]
"""
import sys
import os
import re
import json
import time
import random
import argparse
from typing import Optional
import requests
import feedparser
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin
from dateutil import parser as date_parser
from datetime import datetime

# Dictionnaire global pour suivre la dernière erreur par source
# Clé: nom de la source, Valeur: message d'erreur (str) ou None si succès
SOURCE_LAST_ERROR: dict = {}


def get_source_errors() -> dict:
    """Retourne une copie du dictionnaire des erreurs par source."""
    return dict(SOURCE_LAST_ERROR)


def _record_error(source_name: str, error_msg: str):
    """Enregistre une erreur pour une source donnée."""
    SOURCE_LAST_ERROR[source_name] = error_msg


def _record_success(source_name: str):
    """Marque une source comme ayant réussi (supprime son erreur si existante)."""
    SOURCE_LAST_ERROR.pop(source_name, None)
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.database import SessionLocal, engine, run_migrations  # noqa: E402
from backend.models import Paper, Base  # noqa: E402
from backend import analysis  # noqa: E402
from backend import sources as catalog  # noqa: E402  (single provenance source of truth)
from backend import scrapers  # noqa: E402  (commercial-bank / fund article scrapers)

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

# Rotation de User-Agents pour éviter le blocage par les sites qui refusent
# les scrapers (banques commerciales, portails financiers, etc.)
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


def get_headers(accept: str = "application/rss+xml, application/xml, text/xml, */*") -> dict:
    """Retourne des headers avec un User-Agent aléatoire pour éviter le blocage."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": accept,
    }


HEADERS = get_headers()

# Délai entre deux requêtes de scraping HTML (secondes). À augmenter si des
# sources bloquent encore. 0 = désactivé.
SCRAPE_REQUEST_DELAY = 1.0

# Nombre maximum de tentatives pour une requête HTTP échouée.
HTTP_MAX_RETRIES = 2
HTTP_RETRY_BACKOFF = 5  # secondes

# The trader's arsenal lives in backend/sources.py (the provenance catalog):
# sell-side desks + central banks (by monetary zone) + multilateral + academic.
# Here we pull just the live RSS feeds from it.
SOURCES = catalog.rss_sources()

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
    "Switzerland": ["switzerland", "swiss", "snb", "franc"],
    "Sweden": ["sweden", "swedish", "riksbank", "krona"],
    "India": ["india", "indian", "rbi", "rupee", "mumbai"],
    "Global": ["global", "international", "world", "emerging markets", "cross-border"],
}


def normalize_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_COVER_CACHE: dict = {}


def fetch_cover_image(url: str, timeout: int = 10) -> Optional[str]:
    """Tente d'extraire l'image de couverture (og:image) d'une page source.
    Retourne l'URL absolue ou None en cas d'échec.

    Utilise un cache mémoire pour ne pas re-fetcher la même URL plusieurs fois.
    Ordre de préférence : og:image > twitter:image > twitter:image:src > première balise <img>.
    """
    if not url or BeautifulSoup is None:
        return None
    if url in _COVER_CACHE:
        return _COVER_CACHE[url]

    result = None
    try:
        resp = requests.get(url, headers=get_headers(), timeout=timeout)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "lxml")
            # 1) og:image
            tag = (
                soup.find("meta", attrs={"property": "og:image"})
                or soup.find("meta", attrs={"name": "og:image"})
                or soup.find("meta", attrs={"property": "twitter:image"})
                or soup.find("meta", attrs={"name": "twitter:image"})
                or soup.find("meta", attrs={"property": "twitter:image:src"})
                or soup.find("meta", attrs={"name": "twitter:image:src"})
            )
            if tag and tag.get("content"):
                result = urljoin(url, tag["content"])
            # 2) fallback : première balise <img> avec src
            if not result:
                img = soup.find("img", attrs={"src": True})
                if img:
                    result = urljoin(url, img["src"])
            # 3) fallback : link rel=image_src
            if not result:
                link = soup.find("link", attrs={"rel": "image_src"})
                if link and link.get("href"):
                    result = urljoin(url, link["href"])
    except Exception:
        result = None

    # Valider que l'result ressemble à une URL http(s)
    if result and not result.startswith(("http://", "https://")):
        result = None

    _COVER_CACHE[url] = result
    return result


def extract_cover_from_entry(entry: dict) -> Optional[str]:
    """Extrait une URL d'image depuis une entrée de flux RSS (feedparser).
    Cherche dans : media:content, media:thumbnail, enclosure, links (image),
    et les balises  dans le summary/description.
    Aucune requête HTTP supplémentaire n'est nécessaire."""
    # 1) media:content (RSS media namespace)
    media = entry.get("media_content")
    if media and isinstance(media, list):
        for m in media:
            if isinstance(m, dict) and m.get("url"):
                return m["url"]

    # 2) media:thumbnail
    media_thumb = entry.get("media_thumbnail")
    if media_thumb and isinstance(media_thumb, list):
        for m in media_thumb:
            if isinstance(m, dict) and m.get("url"):
                return m["url"]

    # 3) enclosure avec type image
    enclosures = entry.get("enclosures")
    if enclosures and isinstance(enclosures, list):
        for enc in enclosures:
            if isinstance(enc, dict):
                typ = enc.get("type", "") or ""
                href = enc.get("href") or enc.get("url")
                if href and typ.startswith("image/"):
                    return href

    # 4) links avec type image
    links = entry.get("links", [])
    if links and isinstance(links, list):
        for link in links:
            if isinstance(link, dict):
                typ = link.get("type", "") or ""
                href = link.get("href", "")
                if href and typ.startswith("image/"):
                    return href

    # 5) image dans le summary/description (première balise )
    summary = entry.get("summary", entry.get("description", ""))
    if summary:
        import re as _re
        m = _re.search(r'<img[^>]+src=["\']([^"\']+)', summary)
        if m:
            return m.group(1)

    return None


def guess_tags(text: str, tag_dict: dict) -> list:
    text = (text or "").lower()
    return [tag for tag, kws in tag_dict.items() if any(kw in text for kw in kws)]


def _parse_date(entry):
    """Use the feed's calendar date (year, month, day) if valid and non-future,
    but stamp the time-of-day (HH:MM:SS) with the Mac's local time at the moment
    the article is received into the database."""
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
    valid = [d for d in candidates if d.date() <= now.date()]
    if valid:
        best = max(valid)
        combined = best.replace(hour=now.hour, minute=now.minute, second=now.second, microsecond=0)
        return min(combined, now)
    return now


def add_paper(db, seen, seen_sigs, *, title, source, abstract="", authors=None,
              published_date=None, source_url=None, pdf_url=None, doi=None, image_url=None):
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

    now_local = datetime.now()
    if published_date is not None:
        pub_dt = published_date.replace(
            hour=now_local.hour, minute=now_local.minute, second=now_local.second, microsecond=0
        )
        if pub_dt > now_local:
            pub_dt = now_local
    else:
        pub_dt = now_local

    abstract = re.sub(r"<[^>]+>", "", abstract or "").strip()
    blob = f"{title} {abstract}"
    paper = Paper(
        title=title, authors=authors or source,
        abstract=abstract, published_date=pub_dt,
        source=source,
        country_tags=guess_tags(blob, COUNTRY_KEYWORDS) or ["Global"],
        thematic_tags=guess_tags(blob, THEMATIC_KEYWORDS),
        pdf_url=pdf_url, source_url=source_url, image_url=image_url,
        doi=doi, clean_title_hash=clean_hash, created_at=now_local,
    )
    paper.currency_pairs = analysis.derive_currency_pairs(paper)
    paper.area = analysis.derive_area(paper)
    db.add(paper)
    seen.add(clean_hash)
    seen_sigs.append(sig)
    return True


_HTTP_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".rss_http_cache.json")


def _load_http_cache() -> dict:
    try:
        with open(_HTTP_CACHE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_http_cache(cache: dict):
    try:
        with open(_HTTP_CACHE_PATH, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


def _fetch_rss_worker(item, cache):
    source_name, url = item
    hdrs = dict(get_headers())
    meta = cache.get(url, {})
    if meta.get("etag"):
        hdrs["If-None-Match"] = meta["etag"]
    if meta.get("last_modified"):
        hdrs["If-Modified-Since"] = meta["last_modified"]
    last_err = None
    for attempt in range(HTTP_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=hdrs, timeout=22)
            if resp.status_code == 304:
                _record_success(source_name)
                return source_name, url, "304", None, meta
            resp.raise_for_status()
            new_meta = {
                "etag": resp.headers.get("ETag") or meta.get("etag"),
                "last_modified": resp.headers.get("Last-Modified") or meta.get("last_modified"),
            }
            _record_success(source_name)
            return source_name, url, "200", feedparser.parse(resp.content), new_meta
        except requests.exceptions.Timeout as e:
            last_err = f"timeout après {22 * (attempt + 1)}s"
        except requests.exceptions.HTTPError as e:
            last_err = f"HTTP {e.response.status_code if e.response else '?'}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:80]}"
        if attempt < HTTP_MAX_RETRIES:
            time.sleep(HTTP_RETRY_BACKOFF * (attempt + 1))
    _record_error(source_name, last_err or "erreur inconnue")
    return source_name, url, f"err: {last_err}", None, meta


def _insert_parsed_feed(db, source_name, feed, seen, seen_sigs):
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

        tag_terms = []
        for t in entry.get("tags", []):
            term = t.get("term") if isinstance(t, dict) else getattr(t, "term", None)
            if term:
                tag_terms.append(str(term))
        summary_txt = entry.get("summary", entry.get("description", ""))
        if tag_terms and not summary_txt:
            summary_txt = "Topics: " + ", ".join(tag_terms[:8])

        if add_paper(db, seen, seen_sigs,
                     title=entry.get("title", "Untitled"),
                     authors=entry.get("author", source_name),
                     abstract=summary_txt,
                     published_date=_parse_date(entry), source=source_name,
                     source_url=source_url, pdf_url=pdf_url,
                     doi=entry.get("prism_doi"),
                     image_url=extract_cover_from_entry(entry)):
            added += 1
    db.commit()
    if added > 0:
        _record_success(source_name)
    else:
        # Le flux a répondu mais aucun article n'a été extrait
        _record_error(source_name, "flux accessible mais aucun article extrait")
    return added


def ingest_rss_parallel(db, sources_dict, seen, seen_sigs, max_workers=12):
    cache = _load_http_cache()
    items = list(sources_dict.items())
    print(f"[*] Fetching {len(items)} RSS feeds in parallel (workers={max_workers})...")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(lambda it: _fetch_rss_worker(it, cache), items))

    total_added = 0
    for source_name, url, status, feed, new_meta in results:
        if new_meta:
            cache[url] = new_meta
        if status == "304":
            print(f"    [-] {source_name}: 304 Not Modified")
            continue
        if feed is None:
            print(f"    [!] {source_name}: {status}")
            continue
        added = _insert_parsed_feed(db, source_name, feed, seen, seen_sigs)
        print(f"    + {source_name}: {added} new (of {len(feed.entries)})")
        total_added += added

    _save_http_cache(cache)
    return total_added


def ingest_rss(db, url, source_name, seen, seen_sigs):
    print(f"[*] {source_name}...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        print(f"    [!] feed error: {e}")
        return 0
    added = _insert_parsed_feed(db, source_name, feed, seen, seen_sigs)
    print(f"    + {added} new")
    return added


def scrape_html(db, url, source_name, seen, seen_sigs, *, href_contains, base, min_title=30):
    """Lightweight scraper for institutions with no usable RSS (Bundesbank,
    Banque de France, Deutsche Bank). Grabs publication anchors by href pattern."""
    print(f"[*] {source_name} (scrape)...")

    soup = None

    # --- Tentative 1: requests classique ---
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(url, headers=get_headers(), timeout=25)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
    except requests.exceptions.Timeout as e:
        last_err = f"timeout après 25s (requests)"
    except requests.exceptions.HTTPError as e:
        last_err = f"HTTP {e.response.status_code if e.response else '?'}"
    except Exception as e:
        last_err = f"{type(e).__name__}: {str(e)[:80]}"

    # --- Tentative 2: Playwright fallback si requests a échoué ---
    if soup is None:
        print(f"    [!] requests a échoué ({last_err}), tentative avec Playwright...")
        try:
            from scripts.scrape_playwright import scrape_with_playwright
            html = scrape_with_playwright(url, timeout=30)
            if html:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")
                print(f"    [OK] Playwright a récupéré la page")
            else:
                _record_error(source_name, f"requests: {last_err}; playwright: échec")
                print(f"    [!] Playwright a aussi échoué")
                return 0
        except Exception as e:
            _record_error(source_name, f"requests: {last_err}; playwright: {type(e).__name__}")
            print(f"    [!] Playwright non disponible ou erreur: {e}")
            return 0

    # Délai pour ne pas saturer le serveur cible
    if SCRAPE_REQUEST_DELAY > 0:
        time.sleep(SCRAPE_REQUEST_DELAY)

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
    if added > 0:
        _record_success(source_name)
    else:
        _record_error(source_name, "page accessible mais aucun lien correspondant")
    print(f"    + {added} new")
    return added


def scrape_articles(db, cfg, seen, seen_sigs):
    """Scrape a commercial-bank / fund insight listing into papers (title +
    publication date + origin link). Configs come from the catalog."""
    name = cfg["name"]
    print(f"[*] {name} (articles)...")
    try:
        rows = scrapers.scrape_site(cfg)
    except requests.exceptions.Timeout as e:
        _record_error(name, "timeout lors du scraping")
        print(f"    [!] scrape error: timeout")
        return 0
    except requests.exceptions.HTTPError as e:
        _record_error(name, f"HTTP {e.response.status_code if e.response else '?'}")
        print(f"    [!] scrape error: HTTP {e.response.status_code if e.response else '?'}")
        return 0
    except Exception as e:
        _record_error(name, f"{type(e).__name__}: {str(e)[:80]}")
        print(f"    [!] scrape error: {e}")
        return 0
    added = 0
    for r in rows:
        if add_paper(db, seen, seen_sigs, title=r["title"], source=name,
                     published_date=r["date"], source_url=r["url"]):
            added += 1
    db.commit()
    if added > 0:
        _record_success(name)
    else:
        _record_error(name, "page accessible mais aucun article extrait")
    print(f"    + {added} new")

    # Délai pour ne pas saturer le serveur cible
    if SCRAPE_REQUEST_DELAY > 0:
        time.sleep(SCRAPE_REQUEST_DELAY)

    return added


def ingest_scopus(db, seen, seen_sigs):
    print("[*] Elsevier / SSRN (Scopus)...")
    headers = {"X-ELS-APIKey": ELSEVIER_API_KEY, "Accept": "application/json"}
    headers.update(get_headers())
    params = {
        "query": "TITLE-ABS-KEY(macroeconomics OR monetary OR finance) AND PUBYEAR > 2024",
        "count": 25, "sort": "-coverDate",
    }
    try:
        resp = requests.get(SCOPUS_URL, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        entries = resp.json().get("search-results", {}).get("entry", [])
    except requests.exceptions.Timeout as e:
        _record_error("Elsevier/SSRN", "timeout Scopus API")
        print(f"    [!] Scopus error: timeout")
        return 0
    except requests.exceptions.HTTPError as e:
        _record_error("Elsevier/SSRN", f"HTTP {e.response.status_code if e.response else '?'}")
        print(f"    [!] Scopus error: HTTP {e.response.status_code if e.response else '?'}")
        return 0
    except Exception as e:
        _record_error("Elsevier/SSRN", f"{type(e).__name__}: {str(e)[:80]}")
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
    if added > 0:
        _record_success("Elsevier/SSRN")
    else:
        _record_error("Elsevier/SSRN", "API accessible mais aucun article")
    print(f"    + {added} new")
    return added


# Institutions with no usable RSS -> scraped from their listing pages (catalog).
SCRAPE_SOURCES = catalog.scrape_sources()


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
        total += ingest_rss_parallel(db, catalog.rss_sources(), seen, seen_sigs)
        for cfg in SCRAPE_SOURCES:
            total += scrape_html(db, cfg["url"], cfg["source_name"], seen, seen_sigs,
                                 href_contains=cfg["href_contains"], base=cfg["base"])
        for cfg in catalog.article_scrape_sources():
            total += scrape_articles(db, cfg, seen, seen_sigs)
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
