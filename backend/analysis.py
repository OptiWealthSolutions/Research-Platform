"""Per-paper analysis pipeline: fetch text -> FinBERT sentiment + keywords +
summary -> persist. Shared by the ingestion script and the API endpoints."""
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests

from . import nlp
from .models import Paper

# ---- currency / FX pair mapping (a trader thinks in pairs, not regions) ----
_CCY_BY_REGION = {"US": "USD", "EU": "EUR", "UK": "GBP", "Japan": "JPY", "China": "CNY"}
_TEXT_CCY = {
    "USD": ["dollar", "federal reserve", "united states", "u.s.", "treasury", "fomc"],
    "EUR": ["euro", " ecb", "eurozone", "euro area", "bundesbank"],
    "GBP": ["sterling", "pound", "bank of england", "gilt", "united kingdom", " boe"],
    "JPY": ["yen", "bank of japan", " boj", "japan"],
    "CNY": ["yuan", "renminbi", "pboc", "china", "chinese"],
    "CHF": ["switzerland", "swiss", " snb", "swiss franc"],
    "CAD": ["canada", "canadian", "bank of canada", "loonie"],
    "AUD": ["australia", "australian", " rba", "aussie dollar"],
    "NZD": ["new zealand", "rbnz", "kiwi dollar"],
}
# FX quoting convention priority: base is the higher-priority currency.
_CCY_PRIORITY = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY", "CNY"]
_ALLOWED_PAIRS = {
    "EUR/USD", "GBP/USD", "AUD/USD", "NZD/USD", "USD/JPY", "USD/CHF", "USD/CAD",
    "USD/CNY", "EUR/GBP", "EUR/JPY", "GBP/JPY", "EUR/CHF", "CHF/JPY", "EUR/CAD",
    "EUR/AUD", "AUD/JPY", "GBP/CHF", "DXY",
}


def _pair_name(a, b):
    pa, pb = _CCY_PRIORITY.index(a), _CCY_PRIORITY.index(b)
    base, quote = (a, b) if pa < pb else (b, a)
    return f"{base}/{quote}"


def derive_currency_pairs(paper) -> list:
    """Map a paper to the FX pairs it bears on, from region tags + text."""
    text = f"{paper.title or ''} {paper.abstract or ''}".lower()
    ccys = set()
    for region in (paper.country_tags or []):
        if region in _CCY_BY_REGION:
            ccys.add(_CCY_BY_REGION[region])
    for ccy, kws in _TEXT_CCY.items():
        if any(kw in text for kw in kws):
            ccys.add(ccy)

    pairs = []
    # USD majors first (most traded / most relevant)
    if "USD" in ccys:
        for c in [x for x in _CCY_PRIORITY if x in ccys and x != "USD"]:
            p = _pair_name("USD", c)
            if p in _ALLOWED_PAIRS and p not in pairs:
                pairs.append(p)
    # crosses among non-USD currencies
    non_usd = [c for c in _CCY_PRIORITY if c in ccys and c != "USD"]
    for i in range(len(non_usd)):
        for j in range(i + 1, len(non_usd)):
            p = _pair_name(non_usd[i], non_usd[j])
            if p in _ALLOWED_PAIRS and p not in pairs:
                pairs.append(p)
    # dollar index when USD is the only signal
    if not pairs and "USD" in ccys:
        pairs.append("DXY")
    return pairs[:5]

_PLACEHOLDER_ABSTRACTS = {
    "click link for abstract.", "no abstract available.", "", "untitled",
}

# Source tiers for the trader interest index (market relevance, not prestige).
_TIER_A = ("ing", "ecb press", "ecb blog", "fed press", "fed speeches",
           "bank of england news", "bank of japan", "bank of canada")
_TIER_B = ("ecb research", "feds", "ifdp", "liberty st", "atlanta",
           "bis", "imf", "bank of england")
_HIGH_VALUE_THEMES = {
    "Monetary Policy", "Inflation", "Macro-Finance", "Liquidity",
    "Digital Currency", "Financial Stability",
}
_MAJOR_BLOCS = {"US", "EU", "UK", "Japan", "China"}


def compute_interest(paper) -> int:
    """Trader value index on a 1..5 scale, from metadata + sentiment.
    Pure-Python (no model), so it can backfill the whole DB cheaply."""
    src = (paper.source or "").lower()
    if any(k in src for k in _TIER_A):
        score = 3.0
    elif any(k in src for k in _TIER_B):
        score = 2.0
    else:
        score = 1.0

    themes = set(paper.thematic_tags or [])
    if themes & _HIGH_VALUE_THEMES:
        score += 1.0
    # directional conviction is actionable
    if paper.sentiment_score is not None and abs(paper.sentiment_score) >= 0.4:
        score += 1.0
    # tied to a tradable bloc rather than only "Global"
    if set(paper.country_tags or []) & _MAJOR_BLOCS:
        score += 0.5
    # real, substantive abstract
    ab = (paper.abstract or "").strip().lower()
    if len(ab) > 150 and ab not in _PLACEHOLDER_ABSTRACTS:
        score += 0.5

    return max(1, min(5, round(score)))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
}


def _download_pdf_text(url: str) -> str:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=30)
        r.raise_for_status()
        if "pdf" in r.headers.get("Content-Type", "").lower() or url.lower().endswith(".pdf"):
            return nlp.extract_pdf_text(r.content)
    except requests.RequestException:
        pass
    return ""


def _resolve_pdf_from_landing(source_url: str) -> str:
    """Many feeds give a landing page, not a PDF. Fetch it and find the first
    plausible PDF link, then extract its text."""
    try:
        r = requests.get(source_url, headers=_HEADERS, timeout=25)
        r.raise_for_status()
        if "pdf" in r.headers.get("Content-Type", "").lower():
            return nlp.extract_pdf_text(r.content)  # was a PDF after all
        hrefs = re.findall(r'href=["\']([^"\']+?\.pdf[^"\']*)["\']', r.text, re.I)
        for href in hrefs[:3]:
            pdf_url = urljoin(source_url, href)
            text = _download_pdf_text(pdf_url)
            if len(text) > 400:
                return text
    except requests.RequestException:
        pass
    return ""


def fetch_text_for_paper(paper: Paper) -> str:
    """Best-effort text source, deepest first: direct PDF -> PDF linked from the
    landing page -> abstract."""
    if paper.pdf_url and paper.pdf_url.lower().endswith(".pdf"):
        text = _download_pdf_text(paper.pdf_url)
        if len(text) > 200:
            return text
    if paper.source_url:
        text = _resolve_pdf_from_landing(paper.source_url)
        if len(text) > 400:
            return text
    # Fallback: title + abstract (drop boilerplate placeholders)
    abstract = (paper.abstract or "").strip()
    if abstract.lower() in _PLACEHOLDER_ABSTRACTS:
        abstract = ""
    return f"{paper.title}. {abstract}".strip()


def analyze_paper(paper: Paper) -> dict:
    """Run the full NLP pipeline on one paper and mutate it in place.
    Returns the computed analysis dict."""
    text = fetch_text_for_paper(paper)
    sentiment = nlp.finbert_sentiment(text)
    keywords = nlp.extract_keywords(text, top_n=8)
    summary = nlp.summarize(text, max_sentences=4)

    paper.sentiment_label = sentiment["label"]
    paper.sentiment_score = sentiment["score"]
    paper.sentiment_detail = sentiment["detail"]
    paper.keywords = keywords
    paper.summary = summary
    paper.currency_pairs = derive_currency_pairs(paper)
    paper.interest_score = compute_interest(paper)
    paper.analyzed_at = datetime.utcnow()
    return {
        "sentiment_label": paper.sentiment_label,
        "sentiment_score": paper.sentiment_score,
        "sentiment_detail": paper.sentiment_detail,
        "keywords": paper.keywords,
        "summary": paper.summary,
    }


def recent_unanalyzed(db, days: int = 7):
    """Papers published within `days` that have not been analyzed yet."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    return (
        db.query(Paper)
        .filter(Paper.published_date >= cutoff)
        .filter(Paper.analyzed_at.is_(None))
        .order_by(Paper.published_date.desc())
        .all()
    )


def analyze_recent(db, days: int = 7, limit: int | None = None) -> int:
    """Analyze all recent unanalyzed papers. Returns count processed."""
    papers = recent_unanalyzed(db, days=days)
    if limit:
        papers = papers[:limit]
    done = 0
    for p in papers:
        try:
            analyze_paper(p)
            db.commit()
            done += 1
            print(f"    [+] analyzed: {p.sentiment_label:>8} ({p.sentiment_score:+.2f})  {p.title[:70]}")
        except Exception as e:  # keep going on individual failures
            db.rollback()
            print(f"    [!] analysis failed for {p.id}: {e}")
    return done
