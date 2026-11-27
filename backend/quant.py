"""Quant / Algo research center — live search over academic databases for
quantitative & algorithmic-trading papers, by asset and strategy.

Primary source: arXiv **q-fin** (Quantitative Finance) — clean public API, the
home of trading/microstructure/portfolio/ML-finance research. This feed has a
different job from the macro feed: it is a *discovery* tool to find strategy
ideas to backtest, not a chronological signal stream — so it is query-driven
(asset × strategy × free text) and returns abstracts + PDFs, not sentiment.
"""
import re
import urllib.parse

import requests
import feedparser

ARXIV_API = "http://export.arxiv.org/api/query"
HEADERS = {"User-Agent": "Mozilla/5.0 (macro-research-platform; quant)"}

# arXiv categories relevant to algorithmic trading research.
CATEGORIES = {
    "q-fin.TR": "Trading & Microstructure",
    "q-fin.CP": "Computational Finance",
    "q-fin.PM": "Portfolio Management",
    "q-fin.ST": "Statistical Finance",
    "q-fin.MF": "Mathematical Finance",
    "q-fin.PR": "Pricing of Securities",
    "q-fin.RM": "Risk Management",
}
DEFAULT_CATS = list(CATEGORIES.keys())

# Asset class -> arXiv search terms (OR'd).
ASSETS = {
    "Equities": ["stock", "stocks", "equity", "equities", "S&P 500", "cross-section of returns"],
    "FX": ["foreign exchange", "forex", "currency", "exchange rate", "FX"],
    "Crypto": ["cryptocurrency", "bitcoin", "ethereum", "crypto", "decentralized finance"],
    "Commodities": ["commodity", "commodities", "crude oil", "gold", "energy market"],
    "Rates/Bonds": ["interest rate", "bond", "treasury", "fixed income", "yield curve"],
    "Volatility": ["volatility", "implied volatility", "options", "VIX", "variance swap"],
    "Futures": ["futures", "futures market", "index futures"],
}

# Strategy / method -> arXiv search terms (OR'd).
STRATEGIES = {
    "Momentum": ["momentum", "time series momentum", "trend following"],
    "Mean-Reversion": ["mean reversion", "mean-reverting", "Ornstein-Uhlenbeck"],
    "Stat-Arb / Pairs": ["statistical arbitrage", "pairs trading", "cointegration"],
    "Market-Making": ["market making", "limit order book", "optimal market making"],
    "Factor": ["factor model", "factor investing", "risk factors", "alpha factor"],
    "Machine-Learning": ["machine learning", "deep learning", "neural network"],
    "Reinforcement-Learning": ["reinforcement learning", "deep reinforcement learning"],
    "Execution": ["optimal execution", "market impact", "transaction cost"],
    "Portfolio-Opt": ["portfolio optimization", "mean-variance", "asset allocation"],
    "High-Frequency": ["high frequency trading", "intraday", "tick data"],
}

# auto-tag detected methods (shown as chips on each result)
_METHODS = {
    "Deep Learning": r"deep learning|deep neural", "Reinforcement Learning": r"reinforcement learning",
    "Neural Network": r"neural network", "Transformer": r"transformer", "LSTM": r"\blstm\b",
    "GARCH": r"\bgarch\b", "Kalman Filter": r"kalman", "Random Forest": r"random forest",
    "XGBoost": r"xgboost|gradient boost", "Bayesian": r"bayesian", "Cointegration": r"cointegrat",
    "Copula": r"copula", "Hawkes": r"hawkes", "Stochastic Control": r"stochastic control|hjb",
    "LLM": r"\bllm\b|large language model",
}


def _or_terms(terms):
    """OR a list of arXiv `all:` term clauses, phrases quoted."""
    parts = []
    for t in terms:
        t = t.strip()
        parts.append(f'all:"{t}"' if " " in t else f"all:{t}")
    return "(" + " OR ".join(parts) + ")" if parts else ""


def build_query(q="", cats=None, assets=None, strategies=None):
    cats = cats or DEFAULT_CATS
    clauses = []
    cat_clause = " OR ".join(f"cat:{c}" for c in cats if c in CATEGORIES)
    if cat_clause:
        clauses.append(f"({cat_clause})")
    asset_terms = [t for a in (assets or []) if a in ASSETS for t in ASSETS[a]]
    if asset_terms:
        clauses.append(_or_terms(asset_terms))
    strat_terms = [t for s in (strategies or []) if s in STRATEGIES for t in STRATEGIES[s]]
    if strat_terms:
        clauses.append(_or_terms(strat_terms))
    q = (q or "").strip()
    if q:
        clauses.append(f'all:"{q}"' if " " in q else f"all:{q}")
    return " AND ".join(clauses)


def classify(text):
    t = (text or "").lower()
    assets = [a for a, terms in ASSETS.items() if any(k.lower() in t for k in terms)]
    strats = [s for s, terms in STRATEGIES.items() if any(k.lower() in t for k in terms)]
    methods = [m for m, rx in _METHODS.items() if re.search(rx, t)]
    return {"assets": assets, "strategies": strats, "methods": methods}


def _pdf_from_links(entry):
    for lk in entry.get("links", []):
        if lk.get("type") == "application/pdf":
            return lk.get("href")
    # derive from abs id
    aid = entry.get("id", "")
    m = re.search(r"arxiv\.org/abs/([\w.\-/]+)", aid)
    return f"https://arxiv.org/pdf/{m.group(1)}" if m else None


def search(q="", cats=None, assets=None, strategies=None, max_results=40, sort="submittedDate"):
    """Run an arXiv q-fin search. Returns list of paper dicts."""
    search_query = build_query(q, cats, assets, strategies)
    if not search_query:
        return []
    params = {
        "search_query": search_query, "start": 0,
        "max_results": min(max_results, 100),
        "sortBy": "relevance" if sort == "relevance" else "submittedDate",
        "sortOrder": "descending",
    }
    url = ARXIV_API + "?" + urllib.parse.urlencode(params)
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
    except Exception:
        return []

    out = []
    for e in feed.entries:
        abs_url = e.get("id", "")
        aid = re.sub(r"^https?://arxiv\.org/abs/", "", abs_url)
        primary = ""
        if e.get("arxiv_primary_category"):
            primary = e["arxiv_primary_category"].get("term", "")
        elif e.get("tags"):
            primary = e["tags"][0].get("term", "")
        cats_all = [t.get("term") for t in e.get("tags", []) if t.get("term")]
        abstract = re.sub(r"\s+", " ", e.get("summary", "")).strip()
        cls = classify(f"{e.get('title','')} {abstract}")
        out.append({
            "id": aid, "title": re.sub(r"\s+", " ", e.get("title", "")).strip(),
            "authors": ", ".join(a.get("name", "") for a in e.get("authors", [])[:6]),
            "abstract": abstract,
            "published": e.get("published", "")[:10],
            "updated": e.get("updated", "")[:10],
            "abs_url": abs_url, "pdf_url": _pdf_from_links(e),
            "primary_cat": primary, "categories": cats_all,
            "primary_cat_label": CATEGORIES.get(primary, primary),
            **cls,
        })
    return out
