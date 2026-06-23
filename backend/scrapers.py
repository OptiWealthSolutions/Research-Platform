"""Article scrapers for commercial banks & asset managers (funds).

Most desks have no public RSS, but several publish *server-rendered* insight
listings we can parse. Each config says how to find article links on a listing
page and where the publication date lives (in a parent container, glued into the
link text, or encoded in the URL path). Results flow into the same `papers`
table as everything else, tagged with `category` commercial_bank / fund.

Configs live in `backend/sources.py` (the catalog); this module is the engine.
JS-gated / login-walled desks stay `portal` in the catalog (linked, not pulled).
"""
import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from dateutil import parser as date_parser

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
# ordered: most specific first
_DATE_RES = [
    re.compile(rf"({_MON}[a-z]*\.?\s+\d{{1,2}},?\s+20\d\d)"),      # Jun 12, 2026
    re.compile(rf"(\d{{1,2}}\s+{_MON}[a-z]*\.?\s+20\d\d)"),         # 18 June 2026
    re.compile(r"(\d{1,2}/\d{1,2}/20\d\d)"),                        # 18/06/2026
    re.compile(r"(20\d\d-\d{2}-\d{2})"),                            # 2026-06-18
]
_URL_DATE = re.compile(r"/(20\d\d)[/-](\d{1,2})(?:[/-](\d{1,2}))?")
_URL_DATE2 = re.compile(r"(20\d\d-\d{2}-\d{2})")


def _parse(s, dayfirst=False):
    try:
        d = date_parser.parse(s, dayfirst=dayfirst, fuzzy=True)
        d = d.replace(tzinfo=None) if d.tzinfo else d
        return d if d <= datetime.now() else None
    except Exception:
        return None


def _date_from_parent(a, dayfirst):
    node = a
    for _ in range(4):
        node = node.parent
        if node is None:
            break
        txt = node.get_text(" ", strip=True)
        for rx in _DATE_RES:
            m = rx.search(txt)
            if m:
                d = _parse(m.group(1), dayfirst)
                if d:
                    return d
    return None


def _date_from_text(text, dayfirst):
    for rx in _DATE_RES:
        m = rx.search(text)
        if m:
            d = _parse(m.group(1), dayfirst)
            if d:
                return d, m.group(1)
    return None, None


def _date_from_url(href, dayfirst):
    m = _URL_DATE2.search(href)
    if m:
        d = _parse(m.group(1))
        if d:
            return d
    m = _URL_DATE.search(href)
    if m:
        y, mo, day = m.group(1), m.group(2), m.group(3)
        try:
            return datetime(int(y), int(mo), int(day or 1))
        except Exception:
            return None
    return None


_NOISE_RES = [
    re.compile(r"^(?:IN FOCUS|IN-DEPTH|SNAPSHOT|QUICK TAKE|MARKET VIEWS|PERSPECTIVE|OUTLOOK|VIDEO|PODCAST|ARTICLE|WEBINAR)\b", re.I),
    re.compile(r"\b\d{1,2}(?:-\d{1,2})?\s*min(?:ute)?s?\s*(?:to\s+)?read\b", re.I),   # "6-8 min read" / "3-5 min to read"
    re.compile(r"\bread\s+\d{1,2}\s*min\b", re.I),
    re.compile(rf"\b\d{{1,2}}\s+{_MON}[a-z]*\.?\s+20\d\d\b"),   # inline "18 June 2026"
    re.compile(rf"\b{_MON}[a-z]*\.?\s+\d{{1,2}},?\s+20\d\d\b"),  # inline "Jun 8, 2026"
    re.compile(r"\s*\d{1,2}[-/]\d{1,2}[-/]20\d\d\s*$"),          # trailing "06-19-2026"
    re.compile(r"\s*20\d\d[-/]\d{1,2}[-/]\d{1,2}\s*$"),          # trailing "2026-06-19"
    re.compile(r"\s*\|\s*$"),
    re.compile(r"\b(?:Podcast|Video|Article|More)\s*$", re.I),
]


def _clean_title(t):
    t = re.sub(r"\s+", " ", t or "").strip()
    t = re.sub(r"^\d{1,2}:\d{2}", "", t).strip()       # leading "20:40" video stamp
    for rx in _NOISE_RES:
        t = rx.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–|·")
    # listing anchors often glue the headline to its dek; keep the headline.
    # cut at the first sentence end past a sensible length, else hard-truncate.
    if len(t) > 130:
        m = re.search(r"[.?!]\s", t[40:])
        cut = (40 + m.start() + 1) if m else 130
        t = t[:cut].rstrip(" -–|·")
    return t


def _title_from_anchor(a):
    """Prefer a heading element inside the link; fall back to its full text."""
    h = a.find(["h1", "h2", "h3", "h4", "h5"])
    raw = h.get_text(" ", strip=True) if h and len(h.get_text(strip=True)) >= 12 else a.get_text(" ", strip=True)
    return _clean_title(raw)


def _render_html(url, *, wait, timeout):
    """Fetch a JS-rendered page with headless Chromium (Playwright). Used for
    public-but-client-rendered desks; never for login-walled portals."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-http2"])
        try:
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=timeout * 1000)
            except Exception:
                pass  # some pages never go idle; the fixed wait below still applies
            page.wait_for_timeout(int(wait * 1000))
            return page.content()
        finally:
            browser.close()


def scrape_site(cfg, *, timeout=25):
    """Return a list of {title, url, date} dicts for one institution config.

    cfg keys: url, link_re (regex str), base, date_mode (parent|text|url|none),
    dayfirst (bool), min_title (int), max (int), render (bool, use headless),
    exclude_re (regex str, drop matching titles).
    """
    from bs4 import BeautifulSoup
    link_re = re.compile(cfg["link_re"], re.I)
    exclude_re = re.compile(cfg["exclude_re"], re.I) if cfg.get("exclude_re") else None
    mode = cfg.get("date_mode", "parent")
    dayfirst = cfg.get("dayfirst", False)
    min_title = cfg.get("min_title", 28)
    cap = cfg.get("max", 30)
    base = cfg["base"]

    if cfg.get("render"):
        markup = _render_html(cfg["url"], wait=cfg.get("wait", 4), timeout=timeout)
    else:
        resp = requests.get(cfg["url"], headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        markup = resp.content
    soup = BeautifulSoup(markup, "lxml")

    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not link_re.search(href):
            continue
        full = urljoin(base, href.split("#")[0])
        if full in seen:
            continue
        title = _title_from_anchor(a)
        if exclude_re and exclude_re.search(title):
            continue
        date = None
        if mode == "parent":
            date = _date_from_parent(a, dayfirst)
        elif mode == "text":
            date, stamp = _date_from_text(a.get_text(" ", strip=True), dayfirst)
        elif mode == "url":
            date = _date_from_url(href, dayfirst)
        # require a date when the config demands one (filters nav/footer links)
        if cfg.get("require_date") and not date:
            continue
        if len(title) < min_title:
            continue
        seen.add(full)
        out.append({"title": title, "url": full, "date": date or datetime.now()})
        if len(out) >= cap:
            break
    return out


if __name__ == "__main__":
    # standalone smoke test against the catalog's article-scrape configs
    import sys, os
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from backend import sources as catalog
    for cfg in catalog.article_scrape_sources():
        try:
            rows = scrape_site(cfg)
            dated = sum(1 for r in rows if r["date"].date() != datetime.now().date())
            print(f"[OK] {cfg['name']:22} {len(rows):3} arts ({dated} dated)")
            for r in rows[:2]:
                print(f"        {r['date'].date()}  {r['title'][:60]}")
        except Exception as e:
            print(f"[!!] {cfg['name']:22} {type(e).__name__}: {str(e)[:50]}")
