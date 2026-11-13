"""Model-Context-Protocol (MCP) adapter for LLM interaction with the
Macro Research Terminal backend.

This module exposes FastAPI routes that follow an MCP-inspired design:
- Discovery of available capabilities (what the LLM can do)
- Structured method invocation with typed parameters
- Responses consistent for LLM consumption (links, metadata, status)

The MCP surface mirrors the existing API but is tuned for machine-readable
discovery and calling, e.g. from an LLM agent or a custom frontend.
"""

from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, or_, and_
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import os

from . import models, schemas, sources as catalog, analysis, fxcalls, pdfscrape
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from .database import SessionLocal, engine, get_db

# ---------------------------------------------------------------------------
# FastAPI app dedicated to the MCP surface
# ---------------------------------------------------------------------------

mcp_app = FastAPI(title="Macro Research Terminal - MCP API", description="Model-Context-Protocol interface for LLM interaction")


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------

def get_mcp_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# MCP-capable Pydantic models (request/response shapes)
# ---------------------------------------------------------------------------

class MCPMethodInfo(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]
    response_description: str


class MCPCapability(BaseModel):
    category: str
    methods: List[MCPMethodInfo]


# ---------------------------------------------------------------------------
# 1) Discovery: what can the LLM do?
# ---------------------------------------------------------------------------

# Capabilities exposed to the LLM via /.well-known/mcp-capabilities
# (or simply GET /mcp/capabilities)

CAPABILITIES = {
    "papers_search": MCPMethodInfo(
        name="papers_search",
        description="Search for research papers filtered by categories, currency pairs, assets, date range, importance (sentiment/analysis status), thematic tags, and source kind.",
        parameters={
            "type": "object",
            "properties": {
                "horizon_days": {"type": "integer", "description": "Only papers published in the last N days."},
                "categories": {"type": "array", "items": {"type": "string"}, "description": "Paper categories: central_bank, commercial_bank, fund, multilateral, academic, macro_news."},
                "zone": {"type": "string", "description": "Monetary zone: USD, EUR, GBP, JPY, CHF, CAD, SEK, INR, GLB."},
                "sentiment": {"type": "string", "description": "Sentiment label: positive, negative, neutral."},
                "pair": {"type": "string", "description": "Currency pair filter (e.g. EURUSD, GBPUSD)."},
                "thematic_tags": {"type": "array", "items": {"type": "string"}, "description": "Thematic tags to match."},
                "min_importance": {"type": "integer", "description": "Minimum FinBERT importance score (0-10, if analyzed)."},
                "source_kind": {"type": "string", "description": "Kind of source: rss, scrape, article_scrape, portal."},
                "skip": {"type": "integer", "default": 0, "ge": 0},
                "limit": {"type": "integer", "default": 50, "le": 200},
            },
            "required": [],
            "additionalProperties": False,
        },
        response_description="List of papers with id, title, source, published_date, sentiment_score, sentiment_link, source_url, pdf_url.",
    ),
    "papers_by_asset": MCPMethodInfo(
        name="papers_by_asset",
        description="Get papers filtered by asset class (FX, Equities, Crypto, Commodities, Rates/Bonds, Volatility, Futures). Uses thematic tag / keyword matching against asset labels.",
        parameters={
            "type": "object",
            "properties": {
                "asset": {"type": "string", "enum": ["Equities", "FX", "Crypto", "Commodities", "Rates/Bonds", "Volatility", "Futures"]},
                "horizon_days": {"type": "integer", "description": "Only papers published in the last N days."},
                "limit": {"type": "integer", "default": 50, "le": 200},
            },
            "required": ["asset"],
            "additionalProperties": False,
        },
        response_description="List of papers with asset-class tags and links.",
    ),
    "platform_health": MCPMethodInfo(
        name="platform_health",
        description="Check the health of the platform's sources: overall status (healthy/degraded/error per zone) and per-source last published date, days since, and ingestion errors.",
        parameters={
            "type": "object",
            "properties": {
                "detailed": {"type": "boolean", "description": "Return per-source details.", "default": True},
            },
            "required": [],
            "additionalProperties": False,
        },
        response_description="Overall status + list of sources with last_published, days_since, status (healthy|warning|error), total_docs, last_error.",
    ),
    "quant_search": MCPMethodInfo(
        name="quant_search",
        description="Search arXiv quantitative finance papers by free text, asset class, strategy/method, and sort order. Discovery tool for backtest ideas.",
        parameters={
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Free-text query."},
                "asset": {"type": "string", "description": "Asset class: Equities, FX, Crypto, Commodities, Rates/Bonds, Volatility, Futures."},
                "strategy": {"type": "string", "description": "Strategy: Momentum, Mean-Reversion, Stat-Arb / Pairs, Market-Making, Factor, Machine-Learning, Reinforcement-Learning, Execution, Portfolio-Opt, High-Frequency."},
                "sort": {"type": "string", "description": "Sort by: submittedDate or relevance.", "default": "submittedDate"},
                "limit": {"type": "integer", "default": 40, "le": 100},
            },
            "required": [],
            "additionalProperties": False,
        },
        response_description="List of arXiv papers with title, authors, abstract, pdf_url, primary category, detected assets/methods.",
    ),
    "fx_calls": MCPMethodInfo(
        name="fx_calls",
        description="Extract published FX calls from bank/fund research: institution, currency pair, directional bias, thesis. No fabricated TP/SL.",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Look back window in days (1-730).", "default": 120},
                "pair": {"type": "string", "description": "Filter by currency pair (e.g. EURUSD)."},
                "category": {"type": "string", "description": "Category filter: commercial_bank or fund.", "default": "commercial_bank"},
            },
            "required": [],
            "additionalProperties": False,
        },
        response_description="List of FX calls with paper source, pair, bias, thesis, and link to source paper.",
    ),
    "ingest_trigger": MCPMethodInfo(
        name="ingest_trigger",
        description="Trigger a manual ingestion run (RSS + scraping + optional FinBERT analysis of recent papers).",
        parameters={
            "type": "object",
            "properties": {
                "analyze": {"type": "boolean", "description": "Also run FinBERT analysis on recent papers.", "default": True},
            },
            "required": [],
            "additionalProperties": False,
        },
        response_description="Job ID or status indicator.",
    ),
    # --- Nouvelles capacités --- #
    "get_stats": MCPMethodInfo(
        name="get_stats",
        description="Get statistics about the database: total papers, analyzed papers, sentiment distribution, sources breakdown, and recent papers count.",
        parameters={
            "type": "object",
            "properties": {
                "horizon_days": {"type": "integer", "description": "Lookback window in days (optional).", "default": None},
            },
            "required": [],
            "additionalProperties": False,
        },
        response_description="StatsResponse with total, analyzed, sentiment, sources, last_week.",
    ),
    "get_sources": MCPMethodInfo(
        name="get_sources",
        description="Get the full provenance map: every desk/institution, its kind, category, zone, portal, and document count.",
        parameters={
            "type": "object",
            "properties": {
                "detailed": {"type": "boolean", "description": "Include feed/scrape config details.", "default": True},
            },
            "required": [],
            "additionalProperties": False,
        },
        response_description="SourceCatalogResponse with categories, zones, and sources.",
    ),
    "get_central_banks": MCPMethodInfo(
        name="get_central_banks",
        description="Get recent central-bank output grouped by monetary zone (press, speeches, publications). Powers the Central Banks view.",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Lookback window in days (1-365).", "default": 30},
                "per_zone": {"type": "integer", "description": "Max papers per zone.", "default": 6},
            },
            "required": [],
            "additionalProperties": False,
        },
        response_description="CentralBankResponse with zones and latest papers.",
    ),
    "get_by_institution": MCPMethodInfo(
        name="get_by_institution",
        description="Get recent articles for a category, grouped by institution and sorted by publication date. Powers the Banks and Funds column views.",
        parameters={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Category: commercial_bank | fund | central_bank | multilateral | academic | macro_news."},
                "days": {"type": "integer", "description": "Lookback window in days (1-730).", "default": 180},
                "per": {"type": "integer", "description": "Max papers per institution.", "default": 8},
            },
            "required": ["category"],
            "additionalProperties": False,
        },
        response_description="ByInstitutionResponse with institution groups.",
    ),
    "get_transactions": MCPMethodInfo(
        name="get_transactions",
        description="Extract published FX calls from bank/fund research: institution, currency pair, directional bias, thesis. No fabricated TP/SL.",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Lookback window in days (1-730).", "default": 120},
                "pair": {"type": "string", "description": "Filter by currency pair (e.g. EURUSD).", "default": None},
                "category": {"type": "string", "description": "Category: commercial_bank | fund.", "default": "commercial_bank"},
            },
            "required": [],
            "additionalProperties": False,
        },
        response_description="TransactionsResponse with FX calls.",
    ),
    "get_board": MCPMethodInfo(
        name="get_board",
        description="Get net FinBERT sentiment per currency bloc over a time window. Drives the top cockpit strip.",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Window in days (1-365).", "default": 7},
            },
            "required": [],
            "additionalProperties": False,
        },
        response_description="BoardResponse with blocs sentiment scores.",
    ),
    "get_digest": MCPMethodInfo(
        name="get_digest",
        description="Get net sentiment summary over a set of paper IDs (the user's watchlist).",
        parameters={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "string"}, "description": "List of paper IDs."},
            },
            "required": ["ids"],
            "additionalProperties": False,
        },
        response_description="DigestResponse with net score, by-bloc breakdown, top bullish/bearish.",
    ),
    "get_paper_by_id": MCPMethodInfo(
        name="get_paper_by_id",
        description="Get a single paper by its ID, including full metadata and analysis status.",
        parameters={
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "Paper ID (UUID format)."},
            },
            "required": ["paper_id"],
            "additionalProperties": False,
        },
        response_description="PaperResponse with full paper metadata.",
    ),
    "get_events": MCPMethodInfo(
        name="get_events",
        description="Get the macro event calendar: upcoming central-bank meetings and key releases (FOMC, ECB, BoE, BoJ, CPI, NFP).",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max events to return.", "default": 12},
            },
            "required": [],
            "additionalProperties": False,
        },
        response_description="List of MacroEvent with date, name, bloc, importance, theme, related_count.",
    ),
    "search_by_keywords": MCPMethodInfo(
        name="search_by_keywords",
        description="Advanced search by combined keywords with boolean logic (AND/OR/NOT) and filters.",
        parameters={
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Boolean search query (AND/OR/NOT, phrases in quotes)."},
                "horizon_days": {"type": "integer", "description": "Only papers published in the last N days."},
                "categories": {"type": "array", "items": {"type": "string"}, "description": "Paper categories."},
                "zone": {"type": "string", "description": "Monetary zone filter."},
                "sentiment": {"type": "string", "description": "Sentiment label."},
                "pair": {"type": "string", "description": "Currency pair filter."},
                "limit": {"type": "integer", "description": "Max results.", "default": 50},
            },
            "required": ["q"],
            "additionalProperties": False,
        },
        response_description="List of papers matching the boolean query with filters.",
    ),
}


# ---------------------------------------------------------------------------
# 2) Endpoints
# ---------------------------------------------------------------------------

@mcp_app.get("/.well-known/mcp-capabilities", response_model=Dict[str, MCPMethodInfo])
def mcp_capabilities():
    """MCP capability discovery endpoint.

    The LLM (or its agent) can call this once to learn what methods are
    available, their parameter schemas, and what they return.  Responses are
    MCPMethodInfo objects so the LLM can validate parameters before calling.
    """
    return {k: v for k, v in CAPABILITIES.items()}


@mcp_app.get("/mcp/papers/search")
def mcp_papers_search(
    horizon_days: Optional[int] = Query(None, ge=1, le=365),
    categories: Optional[List[str]] = Query(None,
        description="central_bank | commercial_bank | fund | multilateral | academic | macro_news"),
    zone: Optional[str] = Query(None, description="Monetary zone: USD, EUR, GBP, JPY, CHF, CAD, SEK, INR, GLB"),
    sentiment: Optional[str] = Query(None, description="positive | negative | neutral"),
    pair: Optional[str] = Query(None, description="Currency pair filter, e.g. EURUSD"),
    thematic_tags: Optional[List[str]] = Query(None),
    min_importance: Optional[int] = Query(None, ge=0, le=10),
    source_kind: Optional[str] = Query(None, description="rss | scrape | article_scrape | portal"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_mcp_db),
):
    """Search for research papers with LLM-friendly filtering.

    All parameters are optional.  The LLM can combine them to zero-in on
    exactly the subset of papers it needs.  Results return the paper's id,
    title, source, published date, sentiment (if analyzed), and URLs for
    the source article and any available PDF.
    """
    query = db.query(models.Paper)

    # Drop future-dated garbage rows
    query = query.filter(models.Paper.published_date <= datetime.now())

    if horizon_days:
        cutoff = datetime.now() - timedelta(days=horizon_days)
        query = query.filter(models.Paper.published_date >= cutoff)
    if categories:
        for cat in categories:
            cat_names = catalog.sources_in_category(cat)
            if cat_names:
                query = query.filter(models.Paper.source.in_(cat_names))
    if zone:
        names = [s["name"] for s in catalog.CATALOG if s["zone"] == zone]
        if names:
            query = query.filter(models.Paper.source.in_(names))
    if sentiment:
        query = query.filter(models.Paper.sentiment_label == sentiment.lower())
    if pair:
        query = query.filter(models.Paper.currency_pairs.contains([pair]) if hasattr(models.Paper.currency_pairs, 'contains')
                             else models.Paper.currency_pairs.like(f"%{pair}%"))
    if thematic_tags:
        for tag in thematic_tags:
            query = query.filter(models.Paper.thematic_tags.contains([tag]))

    # Source kind filter (rss / scrape / article_scrape / portal)
    if source_kind:
        src_names = [s["name"] for s in catalog.CATALOG if s.get("kind") == source_kind]
        if src_names:
            query = query.filter(models.Paper.source.in_(src_names))

    # Importance / analysis filtering
    if min_importance is not None:
        query = query.filter(models.Paper.sentiment_score.isnot(None))

    papers = query.order_by(desc(models.Paper.published_date)).offset(skip).limit(limit).all()

    out = []
    for p in papers:
        meta = catalog.META.get(p.source, {})
        out.append({
            "id": p.id,
            "title": p.title,
            "source": p.source,
            "institution": meta.get("institution", p.source),
            "zone": meta.get("zone", "GLB"),
            "category": meta.get("category", "unknown"),
            "published_date": p.published_date.isoformat() if p.published_date else None,
            "sentiment_label": p.sentiment_label,
            "sentiment_score": p.sentiment_score,
            "source_url": p.source_url,
            "pdf_url": p.pdf_url,
            "currency_pairs": p.currency_pairs or [],
            "thematic_tags": p.thematic_tags or [],
        })

    return {
        "count": len(out),
        "papers": out,
        "filters_applied": {
            "horizon_days": horizon_days,
            "categories": categories,
            "zone": zone,
            "sentiment": sentiment,
            "pair": pair,
            "thematic_tags": thematic_tags,
            "min_importance": min_importance,
            "source_kind": source_kind,
        }
    }


@mcp_app.get("/mcp/papers/by-asset")
def mcp_papers_by_asset(
    asset: str = Query(..., description="Equities | FX | Crypto | Commodities | Rates/Bonds | Volatility | Futures"),
    horizon_days: Optional[int] = Query(None, ge=1, le=365),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_mcp_db),
):
    """Get papers filtered by asset class.

    The asset filter uses thematic tag matching against known asset keywords
    (defined in `backend/quant.py`'s ASSETS dict).  Returns papers whose
    thematic_tags or title/abstract content match the asset class.
    """
    from . import quant

    valid_assets = {"Equities", "FX", "Crypto", "Commodities", "Rates/Bonds", "Volatility", "Futures"}
    if asset not in valid_assets:
        raise HTTPException(status_code=400, detail=f"Invalid asset. Must be one of {list(valid_assets)}")

    # Use the existing /api/papers endpoint with a free-text search for asset keywords
    asset_keywords = quant.ASSETS.get(asset, [])
    if not asset_keywords:
        return {"count": 0, "papers": []}

    # Search using the first few keywords as a free-text search
    search_query = " ".join(asset_keywords[:3])  # use a subset to avoid overly broad matches

    query = db.query(models.Paper)
    query = query.filter(models.Paper.published_date <= datetime.now())
    # Simple text search in title/abstract blob
    like = search_query.lower()
    query = query.filter(
        func.lower(models.Paper.title).like(f"%{like}%") |
        func.lower(models.Paper.abstract.like(f"%{like}%"))
    )

    if horizon_days:
        cutoff = datetime.now() - timedelta(days=horizon_days)
        query = query.filter(models.Paper.published_date >= cutoff)

    papers = query.order_by(desc(models.Paper.published_date)).limit(limit).all()

    out = []
    for p in papers:
        meta = catalog.META.get(p.source, {})
        out.append({
            "id": p.id,
            "title": p.title,
            "source": p.source,
            "institution": meta.get("institution", p.source),
            "zone": meta.get("zone", "GLB"),
            "category": meta.get("category", "unknown"),
            "published_date": p.published_date.isoformat() if p.published_date else None,
            "sentiment_label": p.sentiment_label,
            "sentiment_score": p.sentiment_score,
            "source_url": p.source_url,
            "pdf_url": p.pdf_url,
            "currency_pairs": p.currency_pairs or [],
            "thematic_tags": p.thematic_tags or [],
            "asset_filter": asset,
        })

    return {"count": len(out), "papers": out, "asset": asset}


@mcp_app.get("/mcp/platform/health")
def mcp_platform_health(
    detailed: bool = Query(True, description="Return per-source details"),
    db: Session = Depends(get_mcp_db),
):
    """Check the health of the platform's sources.

    MCP-style health check: overall status and per-source details including
    last published date, days since, status (healthy|warning|error), total
    docs in DB, and last ingestion error.
    """
    from scripts import ingest as ingest_mod

    now = datetime.now()
    counts = dict(
        db.query(models.Paper.source, func.count(models.Paper.id))
        .group_by(models.Paper.source).all()
    )
    last_dates = dict(
        db.query(models.Paper.source, func.max(models.Paper.published_date))
        .group_by(models.Paper.source).all()
    )

    # Get source errors
    try:
        from scripts import ingest as ingest_mod
        source_errors = ingest_mod.get_source_errors()
    except Exception:
        source_errors = {}

    sources_health = []
    error_count = 0
    degraded_count = 0

    for s in catalog.CATALOG:
        name = s["name"]
        kind = s.get("kind", "unknown")
        zone = s.get("zone", "GLB")
        total_docs = counts.get(name, 0)
        last_pub = last_dates.get(name)
        last_error = source_errors.get(name)

        if last_pub is None:
            status = "error"
            days_since = None
            last_iso = None
            error_count += 1
        else:
            last_iso = last_pub.isoformat() if hasattr(last_pub, "isoformat") else str(last_pub)
            days_since = (now - last_pub).days
            if days_since <= 7:
                status = "healthy"
            elif days_since <= 30:
                status = "warning"
                degraded_count += 1
            else:
                status = "error"
                error_count += 1

        if detailed:
            sources_health.append({
                "name": name,
                "kind": kind,
                "zone": zone,
                "last_published": last_iso,
                "days_since_last": days_since,
                "status": status,
                "total_docs": total_docs,
                "last_error": last_error,
            })
        else:
            sources_health.append({
                "name": name,
                "status": status,
            })

    # Sort: errors first, then warnings, then healthy by name
    status_order = {"error": 0, "warning": 1, "healthy": 2}
    sources_health.sort(key=lambda x: (status_order.get(x.get("status", "healthy"), 9), x.get("name", "")))

    if error_count > 0:
        overall = "error"
    elif degraded_count > 0:
        overall = "degraded"
    else:
        overall = "healthy"

    result = {
        "overall": overall,
        "sources": sources_health,
    }

    # Add summary by zone
    zones_summary = {}
    for s in sources_health:
        z = s.get("zone", "GLB")
        if z not in zones_summary:
            zones_summary[z] = {"healthy": 0, "warning": 0, "error": 0}
        sz = s.get("status", "healthy")
        if isinstance(sz, str):
            zones_summary[z][sz] += 1

    result["zones_summary"] = zones_summary
    return result


@mcp_app.get("/mcp/quant/search")
def mcp_quant_search(
    q: str = Query("", description="Free text query"),
    asset: Optional[str] = Query(None, description="Asset class: Equities | FX | Crypto | Commodities | Rates/Bonds | Volatility | Futures"),
    strategy: Optional[str] = Query(None, description="Strategy: Momentum | Mean-Reversion | Stat-Arb / Pairs | Market-Making | Factor | Machine-Learning | Reinforcement-Learning | Execution | Portfolio-Opt | High-Frequency"),
    sort: str = Query("submittedDate", description="submittedDate or relevance"),
    limit: int = Query(40, ge=1, le=100),
):
    """Search arXiv quantitative finance papers.

    MCP endpoint for the quant/algo research center.  Returns papers from
    arXiv q-fin with metadata, detected assets/methods, and PDF links.
    """
    from . import quant as qmod

    valid_assets = {"Equities", "FX", "Crypto", "Commodities", "Rates/Bonds", "Volatility", "Futures"}
    valid_strategies = {"Momentum", "Mean-Reversion", "Stat-Arb / Pairs", "Market-Making",
                        "Factor", "Machine-Learning", "Reinforcement-Learning", "Execution",
                        "Portfolio-Opt", "High-Frequency"}

    asset_filter = qmod.ASSETS.get(asset, []) if asset and asset in valid_assets else []
    strategy_filter = qmod.STRATEGIES.get(strategy, []) if strategy and strategy in valid_strategies else []

    papers = qmod.search(q=q, assets=asset_filter, strategies=strategy_filter,
                         max_results=limit, sort=sort)

    # Enhance with category labels
    out = []
    for p in papers:
        # Map arXiv category to our categorization
        primary = p.get("primary_cat", "")
        cat_label = qmod.CATEGORIES.get(primary, primary) if primary else ""

        out.append({
            "id": p.get("aid", ""),
            "title": p.get("title", ""),
            "authors": p.get("authors", ""),
            "abstract": p.get("abstract", ""),
            "published": p.get("published", "")[:10],
            "updated": p.get("updated", "")[:10],
            "abs_url": p.get("abs_url", ""),
            "pdf_url": p.get("pdf_url", ""),
            "primary_category": primary,
            "categories": p.get("categories", []),
            "primary_cat_label": cat_label,
            "detected_assets": p.get("assets", []),
            "detected_strategies": p.get("strategies", []),
            "detected_methods": p.get("methods", []),
        })

    return {"count": len(out), "query": qmod.build_query(q, asset_filter, asset_filter, strategy_filter),
            "papers": out}


@mcp_app.get("/mcp/fx/calls")
def mcp_fx_calls(
    days: int = Query(120, ge=1, le=730),
    pair: Optional[str] = Query(None, description="Currency pair filter, e.g. EURUSD"),
    category: str = Query("commercial_bank", description="commercial_bank | fund"),
):
    """Extract published FX calls from bank/fund research.

    Returns FX calls with institution, currency pair, directional bias (bullish/bearish/neutral),
    thesis (the note's title), and a link to the source paper.
    """
    from . import models, schemas
    from .database import SessionLocal

    db = SessionLocal()
    try:
        now = datetime.now()
        cutoff = now - timedelta(days=days)
        valid = models.Paper.published_date <= now

        cats = [category] if category else ["commercial_bank", "fund"]
        src_names = [s for c in cats for s in catalog.sources_in_category(c)]

        rows = db.query(models.Paper).filter(valid
                                             ).filter(models.Paper.published_date >= cutoff
                                             ).filter(models.Paper.source.in_(src_names)
                                             ).order_by(desc(models.Paper.published_date)).all()

        calls = []
        for p in rows:
            meta = catalog.META.get(p.source, {})
            for call in fxcalls.extract_calls(p):
                if pair and call["pair"] != pair:
                    continue
                calls.append({
                    "paper_id": p.id,
                    "institution": meta.get("institution", p.source),
                    "source": p.source,
                    "category": meta.get("category", "commercial_bank"),
                    "zone": meta.get("zone", "GLB"),
                    "date": p.published_date,
                    "pair": call["pair"],
                    "bias": call["bias"],
                    "score": p.sentiment_score,
                    "thesis": p.title,
                    "url": p.source_url or p.pdf_url,
                })
                if len(calls) >= 200:
                    break
            if len(calls) >= 200:
                break

        return {"count": len(calls), "window_days": days, "pair": pair, "calls": calls}
    finally:
        db.close()


@mcp_app.post("/mcp/ingest/trigger")
def mcp_ingest_trigger(
    analyze: bool = True,
):
    """Trigger a manual ingestion run (RSS + scraping + optional FinBERT analysis).

    MCP endpoint.  Starts a background ingestion job.  Returns a job identifier
    that the LLM can poll or ignore (the platform's scheduler loop will also
    pick up the new data on next cycle).
    """
    import threading

    def _job():
        try:
            from scripts import ingest as ingest_mod
            ingest_mod.run(do_analyze=analyze, days=7)
        except Exception as e:
            print(f"[!] MCP ingest job error: {e}")

    t = threading.Thread(target=_job, daemon=True)
    t.start()

    return {
        "status": "started",
        "message": "Ingestion job triggered in background",
        "analyze": analyze,
        "timestamp": datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Nouvelles capacités endpoints
# ---------------------------------------------------------------------------


@mcp_app.get("/mcp/get_stats")
def mcp_get_stats(
    horizon_days: Optional[int] = Query(None, ge=1, le=365, description="Lookback window in days"),
    db: Session = Depends(get_mcp_db),
):
    """Get statistics about the database."""
    from datetime import timedelta as td
    now = datetime.now()
    valid = models.Paper.published_date <= now

    total = db.query(func.count(models.Paper.id)).filter(valid).scalar() or 0
    analyzed = (
        db.query(func.count(models.Paper.id))
        .filter(valid)
        .filter(models.Paper.analyzed_at.isnot(None))
        .scalar()
        or 0
    )
    week_cutoff = now - td(days=int(horizon_days)) if horizon_days else now - td(days=7)
    last_week = (
        db.query(func.count(models.Paper.id))
        .filter(models.Paper.published_date >= week_cutoff)
        .filter(valid)
        .scalar()
        or 0
    )

    sentiment = {}
    for label, count in (
        db.query(models.Paper.sentiment_label, func.count(models.Paper.id))
        .filter(valid)
        .filter(models.Paper.sentiment_label.isnot(None))
        .group_by(models.Paper.sentiment_label)
        .all()
    ):
        sentiment[label] = count

    sources = {}
    for src, count in (
        db.query(models.Paper.source, func.count(models.Paper.id))
        .filter(valid)
        .group_by(models.Paper.source)
        .all()
    ):
        sources[src or "Unknown"] = count

    return schemas.StatsResponse(
        total=total,
        analyzed=analyzed,
        sentiment=sentiment,
        sources=sources,
        last_week=last_week,
    )


@mcp_app.get("/mcp/get_sources")
def mcp_get_sources(
    detailed: bool = Query(True, description="Include feed/scrape config details"),
    db: Session = Depends(get_mcp_db),
):
    """Get the full provenance map."""
    counts = dict(
        db.query(models.Paper.source, func.count(models.Paper.id))
        .group_by(models.Paper.source).all()
    )
    last_dates = dict(
        db.query(models.Paper.source, func.max(models.Paper.published_date))
        .group_by(models.Paper.source).all()
    )

    # Get source errors
    try:
        from scripts import ingest as ingest_mod
        source_errors = ingest_mod.get_source_errors()
    except Exception:
        source_errors = {}

    items = [
        schemas.SourceItem(
            name=s["name"],
            institution=s["institution"],
            domain=s["domain"],
            kind=s["kind"],
            category=s["category"],
            zone=s["zone"],
            portal=s["portal"],
            feed=s["feed"],
            count=counts.get(s["name"], 0),
        )
        for s in catalog.CATALOG
    ]

    # Add last_published and error info
    for item in items:
        item.last_published = last_dates.get(item.name)
        item.last_error = source_errors.get(item.name)
        # Calculate days since
        last_pub = last_dates.get(item.name)
        if last_pub:
            from datetime import datetime as dt
            now = dt.now()
            item.days_since_last = (now - last_pub).days
            if item.days_since_last <= 7:
                item.status = "healthy"
            elif item.days_since_last <= 30:
                item.status = "warning"
            else:
                item.status = "error"
        else:
            item.status = "error"
            item.days_since_last = None

    if detailed:
        return schemas.SourceCatalogResponse(
            categories=catalog.CATEGORIES,
            zones=catalog.ZONES,
            sources=items,
        )
    else:
        # Return simplified version
        simple = [
            {
                "name": item.name,
                "kind": item.kind,
                "zone": item.zone,
                "count": item.count,
                "status": item.status,
            }
            for item in items
        ]
        return {"sources": simple, "count": len(items)}


@mcp_app.get("/mcp/get_central_banks")
def mcp_get_central_banks(
    days: int = Query(30, ge=1, le=365),
    per_zone: int = Query(6, ge=1, le=30),
    db: Session = Depends(get_mcp_db),
):
    """Recent central-bank output grouped by monetary zone."""
    from . import sources as catalog

    now = datetime.now()
    cutoff = now - timedelta(days=days)
    cb_sources = catalog.sources_in_category("central_bank")
    rows = (
        db.query(models.Paper)
        .filter(models.Paper.source.in_(cb_sources))
        .filter(models.Paper.published_date <= now)
        .filter(models.Paper.published_date >= cutoff)
        .order_by(desc(models.Paper.published_date))
        .all()
    )
    by_zone = {}
    for p in rows:
        by_zone.setdefault(catalog.zone_of(p.source), []).append(p)

    zones_out = []
    for code in catalog.ZONE_ORDER:
        meta = catalog.ZONES.get(code)
        if not meta:
            continue
        zone_srcs = [s for s in catalog.CATALOG
                     if s["zone"] == code and s["category"] == "central_bank"]
        if not zone_srcs:
            continue
        papers = by_zone.get(code, [])
        scored = [p for p in papers if p.sentiment_score is not None]
        net = round(sum(p.sentiment_score for p in scored) / len(scored), 4) if scored else None
        latest = [
            schemas.CBItem(
                id=p.id, title=p.title, source=p.source,
                published_date=p.published_date,
                sentiment_label=p.sentiment_label, sentiment_score=p.sentiment_score,
                source_url=p.source_url, pdf_url=p.pdf_url,
            )
            for p in papers[:per_zone]
        ]
        zones_out.append(schemas.CBZone(
            zone=code, label=meta["label"], ccy=meta["ccy"], flag=meta["flag"],
            authority=meta["authority"], count=len(papers), net_score=net,
            institutions=sorted({s["institution"] for s in zone_srcs}),
            latest=latest,
        ))
    return schemas.CentralBankResponse(window_days=days, zones=zones_out)


@mcp_app.get("/mcp/get_by_institution")
def mcp_get_by_institution(
    category: str = Query(..., description="commercial_bank | fund | central_bank | multilateral | academic | macro_news"),
    days: int = Query(180, ge=1, le=730),
    per: int = Query(8, ge=1, le=40),
    db: Session = Depends(get_mcp_db),
):
    """Recent articles grouped by institution."""
    from . import sources as catalog

    now = datetime.now()
    cutoff = now - timedelta(days=days)
    cat_sources = catalog.sources_in_category(category)
    rows = (
        db.query(models.Paper)
        .filter(models.Paper.source.in_(cat_sources))
        .filter(models.Paper.published_date <= now)
        .filter(models.Paper.published_date >= cutoff)
        .order_by(desc(models.Paper.published_date))
        .all()
    )
    # group by institution
    groups = {}
    for p in rows:
        meta = catalog.META.get(p.source, {})
        inst = meta.get("institution", p.source)
        groups.setdefault(inst, []).append(p)

    out = []
    for inst, papers in groups.items():
        meta = catalog.META.get(papers[0].source, {})
        scored = [p for p in papers if p.sentiment_score is not None]
        net = round(sum(p.sentiment_score for p in scored) / len(scored), 4) if scored else None
        latest = [
            schemas.CBItem(
                id=p.id, title=p.title, source=p.source,
                published_date=p.published_date,
                sentiment_label=p.sentiment_label, sentiment_score=p.sentiment_score,
                source_url=p.source_url, pdf_url=p.pdf_url,
            )
            for p in papers[:per]
        ]
        out.append(schemas.InstitutionGroup(
            institution=inst,
            zone=meta.get("zone", "GLB"),
            kind=meta.get("kind", "portal"),
            portal=meta.get("portal"),
            count=len(papers),
            net_score=net,
            latest=latest,
        ))
    out.sort(key=lambda g: g.count, reverse=True)
    return schemas.ByInstitutionResponse(
        window_days=days, category=category,
        label=catalog.CATEGORIES.get(category, category), groups=out,
    )


@mcp_app.get("/mcp/get_transactions")
def mcp_get_transactions(
    days: int = Query(120, ge=1, le=730),
    pair: Optional[str] = Query(None, description="Currency pair filter, e.g. EURUSD"),
    category: str = Query("commercial_bank", description="commercial_bank | fund"),
    db: Session = Depends(get_mcp_db),
):
    """Extract published FX calls from bank/fund research."""
    from . import models, schemas
    from .database import SessionLocal

    db_local = SessionLocal()
    try:
        now = datetime.now()
        cutoff = now - timedelta(days=days)
        valid = models.Paper.published_date <= now
        cats = [category] if category else ["commercial_bank", "fund"]
        src_names = [s for c in cats for s in catalog.sources_in_category(c)]

        rows = db_local.query(models.Paper).filter(valid
                                                     ).filter(models.Paper.published_date >= cutoff
                                                     ).filter(models.Paper.source.in_(src_names)
                                                     ).order_by(desc(models.Paper.published_date)).all()

        calls = []
        for p in rows:
            meta = catalog.META.get(p.source, {})
            for call in fxcalls.extract_calls(p):
                if pair and call["pair"] != pair:
                    continue
                calls.append(schemas.FxCall(
                    paper_id=p.id, institution=meta.get("institution", p.source),
                    source=p.source, category=meta.get("category", "commercial_bank"),
                    zone=meta.get("zone", "GLB"), date=p.published_date,
                    pair=call["pair"], bias=call["bias"], score=p.sentiment_score,
                    thesis=p.title, url=p.source_url or p.pdf_url,
                ))
                if len(calls) >= 200:
                    break
            if len(calls) >= 200:
                break
        return schemas.TransactionsResponse(window_days=days, count=len(calls), calls=calls)
    finally:
        db_local.close()


@mcp_app.get("/mcp/get_board")
def mcp_get_board(
    days: int = Query(7, ge=1, le=365),
    db: Session = Depends(get_mcp_db),
):
    """Net FinBERT sentiment per currency bloc."""
    from . import models

    now = datetime.now()
    cutoff = now - timedelta(days=days)
    rows = (
        db.query(models.Paper)
        .filter(models.Paper.analyzed_at.isnot(None))
        .filter(models.Paper.published_date >= cutoff)
        .filter(models.Paper.published_date <= now)
        .all()
    )
    CURRENCY_BLOCS = [
        ("USD", "US"),
        ("EUR", "EU"),
        ("GBP", "UK"),
        ("JPY", "Japan"),
        ("CNY", "China"),
        ("GLB", "Global"),
    ]
    blocs = []
    for code, region in CURRENCY_BLOCS:
        matched = [r for r in rows if region in (r.country_tags or [])]
        n = len(matched)
        if n:
            scores = [r.sentiment_score or 0.0 for r in matched]
            mean = sum(scores) / n
        else:
            mean = 0.0
        blocs.append(schemas.BlocSentiment(
            bloc=code, region=region, score=round(mean, 4), count=n,
            positive=sum(1 for r in matched if r.sentiment_label == "positive"),
            negative=sum(1 for r in matched if r.sentiment_label == "negative"),
            neutral=sum(1 for r in matched if r.sentiment_label == "neutral"),
        ))
    return schemas.BoardResponse(window_days=days, blocs=blocs)


@mcp_app.post("/mcp/get_digest")
def mcp_get_digest(
    req: schemas.DigestRequest,
    db: Session = Depends(get_mcp_db),
):
    """Net sentiment summary over a set of paper IDs (the user's watchlist)."""
    papers = (
        db.query(models.Paper).filter(models.Paper.id.in_(req.ids)).all()
        if req.ids else []
    )
    scored = [p for p in papers if p.sentiment_score is not None]
    net = round(sum(p.sentiment_score for p in scored) / len(scored), 4) if scored else 0.0
    label = "bullish" if net > 0.03 else "bearish" if net < -0.03 else "neutral"

    bloc_acc = {}
    for p in scored:
        for code, region in [("USD", "US"), ("EUR", "EU"), ("GBP", "UK"), ("JPY", "Japan"), ("CNY", "China"), ("GLB", "Global")]:
            if region in (p.country_tags or []):
                bloc_acc.setdefault(code, []).append(p.sentiment_score)
    by_bloc = [
        schemas.DigestBloc(bloc=c, score=round(sum(v) / len(v), 4), count=len(v))
        for c, v in bloc_acc.items()
    ]
    by_bloc.sort(key=lambda x: x.score)

    def brief(p):
        return {"id": p.id, "title": p.title, "source": p.source,
                "score": p.sentiment_score, "label": p.sentiment_label}
    ranked = sorted(scored, key=lambda p: p.sentiment_score, reverse=True)
    return schemas.DigestResponse(
        count=len(papers), analyzed=len(scored), net_score=net, net_label=label,
        positive=sum(1 for p in scored if p.sentiment_label == "positive"),
        negative=sum(1 for p in scored if p.sentiment_label == "negative"),
        neutral=sum(1 for p in scored if p.sentiment_label == "neutral"),
        by_bloc=by_bloc,
        top_bullish=[brief(p) for p in ranked[:3]],
        top_bearish=[brief(p) for p in ranked[-3:][::-1]],
        generated_at=datetime.now(),
    )


@mcp_app.get("/mcp/get_paper_by_id")
def mcp_get_paper_by_id(
    paper_id: str = Query(..., description="Paper ID (UUID)"),
    db: Session = Depends(get_mcp_db),
):
    """Get a single paper by ID."""
    paper = db.query(models.Paper).filter(models.Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    meta = catalog.META.get(paper.source, {})
    return {
        "id": paper.id,
        "title": paper.title,
        "authors": paper.authors,
        "abstract": paper.abstract,
        "published_date": paper.published_date.isoformat() if paper.published_date else None,
        "source": paper.source,
        "institution": meta.get("institution", paper.source),
        "zone": meta.get("zone", "GLB"),
        "category": meta.get("category", "unknown"),
        "sentiment_label": paper.sentiment_label,
        "sentiment_score": paper.sentiment_score,
        "source_url": paper.source_url,
        "pdf_url": paper.pdf_url,
        "currency_pairs": paper.currency_pairs or [],
        "thematic_tags": paper.thematic_tags or [],
        "created_at": paper.created_at.isoformat() if paper.created_at else None,
        "analyzed_at": paper.analyzed_at.isoformat() if paper.analyzed_at else None,
        "thesis": paper.thesis,
        "thesis_tags": paper.thesis_tags or [],
    }


@mcp_app.get("/mcp/get_events")
def mcp_get_events(
    limit: int = Query(12, ge=1, le=40),
    db: Session = Depends(get_mcp_db),
):
    """Get the macro event calendar."""
    from datetime import datetime as dt, timedelta

    today = dt.now().date()
    recent_cut = dt.now() - timedelta(days=21)
    recent = (
        db.query(models.Paper)
        .filter(models.Paper.published_date >= recent_cut)
        .filter(models.Paper.published_date <= dt.now())
        .all()
    )
    MACRO_CALENDAR = [
        ("2026-07-03", "US Non-Farm Payrolls", "USD", "US", "high", "Labor Market"),
        ("2026-07-14", "US CPI (June)", "USD", "US", "high", "Inflation"),
        ("2026-07-23", "ECB Governing Council", "EUR", "EU", "high", "Monetary Policy"),
        ("2026-07-29", "FOMC Decision", "USD", "US", "high", "Monetary Policy"),
        ("2026-07-30", "BoJ Policy Meeting", "JPY", "Japan", "high", "Monetary Policy"),
        ("2026-08-06", "BoE MPC Decision", "GBP", "UK", "high", "Monetary Policy"),
        ("2026-08-07", "US Non-Farm Payrolls", "USD", "US", "high", "Labor Market"),
        ("2026-08-12", "US CPI (July)", "USD", "US", "high", "Inflation"),
        ("2026-09-04", "US Non-Farm Payrolls", "USD", "US", "high", "Labor Market"),
        ("2026-09-10", "ECB Governing Council", "EUR", "EU", "high", "Monetary Policy"),
        ("2026-09-11", "US CPI (August)", "USD", "US", "high", "Inflation"),
        ("2026-09-16", "FOMC Decision", "USD", "US", "high", "Monetary Policy"),
        ("2026-09-17", "BoE MPC Decision", "GBP", "UK", "high", "Monetary Policy"),
        ("2026-09-18", "BoJ Policy Meeting", "JPY", "Japan", "high", "Monetary Policy"),
        ("2026-10-02", "US Non-Farm Payrolls", "USD", "US", "high", "Labor Market"),
        ("2026-10-14", "US CPI (September)", "USD", "US", "high", "Inflation"),
        ("2026-10-28", "FOMC Decision", "USD", "US", "high", "Monetary Policy"),
        ("2026-10-29", "ECB Governing Council", "EUR", "EU", "high", "Monetary Policy"),
        ("2026-10-30", "BoJ Policy Meeting", "JPY", "Japan", "high", "Monetary Policy"),
        ("2026-11-05", "BoE MPC Decision", "GBP", "UK", "high", "Monetary Policy"),
        ("2026-11-06", "US Non-Farm Payrolls", "USD", "US", "high", "Labor Market"),
        ("2026-11-13", "US CPI (October)", "USD", "US", "high", "Inflation"),
        ("2026-12-04", "US Non-Farm Payrolls", "USD", "US", "high", "Labor Market"),
        ("2026-12-10", "US CPI (November)", "USD", "US", "high", "Inflation"),
        ("2026-12-16", "FOMC Decision", "USD", "US", "high", "Monetary Policy"),
        ("2026-12-17", "ECB Governing Council", "EUR", "EU", "high", "Monetary Policy"),
        ("2026-12-18", "BoJ Policy Meeting", "JPY", "Japan", "high", "Monetary Policy"),
    ]

    out = []
    for date_s, name, bloc, region, importance, theme in MACRO_CALENDAR:
        d = dt.strptime(date_s, "%Y-%m-%d").date()
        if d < today:
            continue
        related = sum(
            1 for p in recent
            if region in (p.country_tags or []) and theme in (p.thematic_tags or [])
        )
        out.append(schemas.MacroEvent(
            date=date_s, days_until=(d - today).days, name=name, bloc=bloc,
            region=region, importance=importance, theme=theme, related_count=related,
        ))
        if len(out) >= limit:
            break
    return out


@mcp_app.get("/mcp/search_by_keywords")
def mcp_search_by_keywords(
    q: str = Query(..., description="Boolean search query (AND/OR/NOT, phrases in quotes)"),
    horizon_days: Optional[int] = Query(None, ge=1, le=365),
    categories: Optional[List[str]] = Query(None,
        description="central_bank | commercial_bank | fund | multilateral | academic | macro_news"),
    zone: Optional[str] = Query(None, description="Monetary zone: USD, EUR, GBP, JPY, CHF, CAD, SEK, INR, GLB"),
    sentiment: Optional[str] = Query(None, description="positive | negative | neutral"),
    pair: Optional[str] = Query(None, description="Currency pair filter, e.g. EURUSD"),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_mcp_db),
):
    """Advanced search by combined keywords with boolean logic and filters."""
    from . import analysis as an_mod

    query = db.query(models.Paper)

    # Drop future-dated rows
    query = query.filter(models.Paper.published_date <= datetime.now())

    # Apply horizon filter
    if horizon_days:
        cutoff = datetime.now() - timedelta(days=horizon_days)
        query = query.filter(models.Paper.published_date >= cutoff)

    # Apply category filter
    if categories:
        for cat in categories:
            cat_names = catalog.sources_in_category(cat)
            if cat_names:
                query = query.filter(models.Paper.source.in_(cat_names))

    # Apply zone filter
    if zone:
        names = [s["name"] for s in catalog.CATALOG if s["zone"] == zone]
        if names:
            query = query.filter(models.Paper.source.in_(names))

    # Apply sentiment filter
    if sentiment:
        query = query.filter(models.Paper.sentiment_label == sentiment.lower())

    # Apply pair filter
    if pair:
        query = query.filter(models.Paper.currency_pairs.contains([pair]) if hasattr(models.Paper.currency_pairs, 'contains')
                             else models.Paper.currency_pairs.like(f"%{pair}%"))

    # Tokenize and evaluate boolean search
    try:
        tokens = an_mod._tokenize_search(q)
        # Apply evaluation - match papers where the boolean expression is true
        def _eval_search(p, tokens, pos=0):
            def parse_or(pos):
                node, pos = an_mod.parse_and(pos)
                while pos < len(tokens) and tokens[pos] == ("OP", "OR"):
                    pos += 1
                    rhs, pos = an_mod.parse_and(pos)
                    node = ("OR", node, rhs)
                return node, pos

            def parse_and(pos):
                node, pos = an_mod.parse_not(pos)
                while pos < len(tokens) and tokens[pos] == ("OP", "AND"):
                    pos += 1
                    rhs, pos = an_mod.parse_not(pos)
                    node = ("AND", node, rhs)
                return node, pos

            def parse_not(pos):
                if pos < len(tokens) and tokens[pos] == ("OP", "NOT"):
                    pos += 1
                    node, pos = parse_not(pos)
                    return ("NOT", node), pos
                return an_mod.parse_primary(pos)

            def parse_primary(pos):
                if pos >= len(tokens):
                    return None, pos
                typ, val = tokens[pos]
                if typ == "OP" and val == "(":
                    node, pos = parse_or(pos + 1)
                    if pos < len(tokens) and tokens[pos] == ("OP", ")"):
                        pos += 1
                    return node, pos
                return (typ, val), pos + 1

            tree, _ = parse_or(pos)

            def match(node):
                if node is None:
                    return True
                kind = node[0]
                if kind == "TERM":
                    text = (str(p.title or "") + " " + str(p.abstract or "") + " " + str(p.source or "")).lower()
                    return node[1] in text
                if kind == "PHRASE":
                    text = (str(p.title or "") + " " + str(p.abstract or "")).lower()
                    return node[1] in text
                if kind == "NOT":
                    return not match(node[1])
                if kind == "AND":
                    return match(node[1]) and match(node[2])
                if kind == "OR":
                    return match(node[1]) or match(node[2])
                return True

            return match(tree)

        # Filter papers using boolean search
        filtered = []
        for p in query.all():
            if _eval_search(p, tokens):
                filtered.append(p)
        papers = filtered[:limit]
    except Exception:
        # Fallback: simple string search
        like = q.lower()
        papers = query.filter(
            func.lower(models.Paper.title).like(f"%{like}%") |
            func.lower(models.Paper.abstract.like(f"%{like}%"))
        ).limit(limit).all()

    out = []
    for p in papers:
        meta = catalog.META.get(p.source, {})
        out.append({
            "id": p.id,
            "title": p.title,
            "source": p.source,
            "institution": meta.get("institution", p.source),
            "zone": meta.get("zone", "GLB"),
            "category": meta.get("category", "unknown"),
            "published_date": p.published_date.isoformat() if p.published_date else None,
            "sentiment_label": p.sentiment_label,
            "sentiment_score": p.sentiment_score,
            "source_url": p.source_url,
            "pdf_url": p.pdf_url,
        })

    return {
        "count": len(out),
        "query": q,
        "papers": out,
    }


# ---------------------------------------------------------------------------
# Health check for the MCP server itself
# ---------------------------------------------------------------------------

@mcp_app.get("/mcp/status")
def mcp_status():
    """MCP server status."""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
        "endpoints": [
            "/.well-known/mcp-capabilities",
            "/mcp/papers/search",
            "/mcp/papers/by-asset",
            "/mcp/platform/health",
            "/mcp/quant/search",
            "/mcp/fx/calls",
            "/mcp/ingest/trigger",
            "/mcp/status",
        ],
    }


# ---------------------------------------------------------------------------
# Root catch-all for quick discovery
# ---------------------------------------------------------------------------

@mcp_app.get("/")
def mcp_root():
    """Root endpoint returning MCP capability summary."""
    return {
        "name": "Macro Research Terminal - MCP API",
        "description": "Model-Context-Protocol interface for LLM interaction with the "
                       "macro research aggregator platform.",
        "capabilities_count": len(CAPABILITIES),
        "discovery": "/.well-known/mcp-capabilities",
    }