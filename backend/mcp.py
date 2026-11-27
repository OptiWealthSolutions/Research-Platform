"""LLM surface of the Macro Research Terminal (HTTP side of the MCP).

Mounted on the main app as an APIRouter. Every capability listed in
`CAPABILITIES` is served at `/mcp/<name>` (the path the documentation gives);
the pre-2026-11-13 paths (`/mcp/papers/search`, `/mcp/platform/health`, …) stay
as aliases. The stdio MCP server that Claude talks to lives in Models-Macro
(`src/research_mcp/server.py`) and calls these routes on 127.0.0.1:8001.

Fixes against the previous version:
  * several `categories` were AND-ed (intersection -> always empty): now a union;
  * `pair` / `thematic_tags` filters ran SQL LIKE on a JSON list and pairs were
    compared as "EURUSD" against stored "EUR/USD": now normalised in Python;
  * `search_by_keywords` imported a tokenizer that did not exist and silently
    fell back to a raw LIKE on the whole query: now `backend.search`;
  * `papers_by_asset` LIKE-d "fx forex currency" as ONE string: now any keyword;
  * `min_importance` was a no-op: now impact_score >= 10 x min_importance;
  * the event calendar was typed by hand: now ForexFactory (`backend.events`).
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session, defer

from . import events as ev
from . import embedding_engine, fulltext, fxcalls, macrofacts, models, schemas, search
from . import sources as catalog
from .database import get_db
from .timeutil import available_at, iso_z, utcnow

from embedding_prop import (
    DocumentInput,
    SentimentRecord,
    classify_doc_kind,
    compute_market_sentiment_indicator,
    format_llm_record,
)

router = APIRouter()

CURRENCY_BLOCS = [("USD", "US"), ("EUR", "EU"), ("GBP", "UK"), ("JPY", "Japan"),
                  ("CHF", "Switzerland"), ("CAD", "Canada"), ("AUD", "Australia"),
                  ("NZD", "New Zealand"), ("CNY", "China"), ("GLB", "Global")]
ASSET_KEYWORDS = {
    "FX": ["forex", "fx ", "currency", "currencies", "exchange rate", "dollar", "euro", "yen",
           "sterling", "franc", "carry trade"],
    "Rates/Bonds": ["yield", "bond", "treasur", "gilt", "bund", "jgb", "rate cut", "rate hike",
                    "term premium", "duration", "curve"],
    "Equities": ["equit", "stock", "s&p", "nasdaq", "earnings", "shares"],
    "Commodities": ["oil", "crude", "brent", "wti", "gold", "copper", "commodit", "iron ore", "lng"],
    "Crypto": ["bitcoin", "crypto", "stablecoin", "ethereum", "blockchain"],
    "Volatility": ["volatility", "vix", "implied vol", "option"],
    "Futures": ["futures", "contango", "backwardation", "roll yield"],
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def ref_of(paper_id: str) -> str:
    return "RP-" + paper_id.replace("-", "")[:8]


def norm_pair(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    s = p.upper().replace("/", "").replace("-", "").replace(" ", "")
    if s == "DXY":
        return "DXY"
    return f"{s[:3]}/{s[3:6]}" if len(s) == 6 else p.upper()


def _zone_ok(p, zone: str) -> bool:
    return catalog.zone_of(p.source) == zone or zone in (p.area or [])


def _infer_zones_for_row(p) -> list[str]:
    if p.area:
        return [str(z).upper() for z in p.area]
    zones = []
    for code, region in CURRENCY_BLOCS:
        if region in (p.country_tags or []):
            zones.append(code)
    src_z = catalog.zone_of(p.source)
    if src_z and src_z not in zones:
        zones.append(src_z)
    return zones or ["GLB"]


def _infer_doc_kind(p) -> str:
    if getattr(p, "doc_kind", None) in ("news", "paper"):
        return p.doc_kind
    return classify_doc_kind(
        DocumentInput(
            id=str(p.id),
            title=str(p.title or ""),
            source=str(p.source or "Unknown"),
            text_source="abstract",
        )
    )


def build_market_sentiment(
    db: Session,
    *,
    days: int = 7,
    lookback_days: int = 90,
    half_life_hours: float = 72.0,
) -> dict:
    """Compute the normalized, percentile-ranked Market Sentiment Indicator from research.db."""
    now = utcnow()
    cutoff = now - timedelta(days=int(days) + int(lookback_days) + 2)
    rows = (
        db.query(
            models.Paper.id,
            models.Paper.title,
            models.Paper.source,
            models.Paper.published_date,
            models.Paper.sentiment_score,
            models.Paper.sentiment_label,
            models.Paper.impact_score,
            models.Paper.area,
            models.Paper.country_tags,
            models.Paper.doc_kind,
            models.Paper.analysis_engine,
            models.Paper.analysis_tier,
        )
        .filter(models.Paper.sentiment_score.isnot(None))
        .filter(models.Paper.published_date >= cutoff)
        .filter(models.Paper.published_date <= now)
        .filter(
            or_(
                models.Paper.published_precision.is_(None),
                models.Paper.published_precision != "suspect",
            )
        )
        .all()
    )

    records = [
        SentimentRecord(
            published_date=r.published_date,
            sentiment_score=float(r.sentiment_score),
            impact_score=float(r.impact_score) if r.impact_score is not None else 50.0,
            sentiment_label=r.sentiment_label,
            zones=_infer_zones_for_row(r),
            doc_kind=_infer_doc_kind(r),
            analysis_engine=r.analysis_engine,
            analysis_tier=r.analysis_tier,
        )
        for r in rows
        if r.published_date is not None
    ]
    ind = compute_market_sentiment_indicator(
        records,
        now=now,
        window_days=days,
        lookback_days=lookback_days,
        half_life_hours=half_life_hours,
    )
    return ind.model_dump()


def brief(p, *, summary_chars: int = 400) -> dict:
    meta = catalog.META.get(p.source, {})
    av = available_at(p.published_date, p.published_precision, p.created_at)
    depth = getattr(p, "content_depth", None) or (
        "full_text" if getattr(p, "text_source", None) in ("pdf", "html") else "snippet_only"
    )
    kind = _infer_doc_kind(p)
    row_dict = {
        "id": p.id,
        "ref": ref_of(p.id),
        "title": p.title,
        "source": p.source,
        "doc_kind": kind,
        "content_depth": depth,
        "analysis_tier": getattr(p, "analysis_tier", None),
        "published_date": iso_z(p.published_date),
        "available_at": iso_z(av),
        "impact_score": p.impact_score,
        "sentiment_label": p.sentiment_label,
        "sentiment_score": p.sentiment_score,
        "area": p.area or [],
        "currency_pairs": p.currency_pairs or [],
        "asset_impacts": getattr(p, "asset_impacts", None) or [],
        "quant_taxonomy": getattr(p, "quant_taxonomy", None) or {},
        "summary": (p.summary or p.abstract or "")[:summary_chars] or None,
        "source_url": p.source_url,
        "pdf_url": p.pdf_url,
    }
    return {
        "id": p.id,
        "ref": ref_of(p.id),
        "title": p.title,
        "source": p.source,
        "institution": meta.get("institution", p.source),
        "category": meta.get("category", "unknown"),
        "zone": meta.get("zone", "GLB"),
        "area": p.area or [],
        "doc_kind": kind,
        "content_depth": depth,
        "analysis_tier": getattr(p, "analysis_tier", None),
        "analysis_engine": getattr(p, "analysis_engine", None),
        "published_date": iso_z(p.published_date),
        "published_precision": p.published_precision,
        "available_at": iso_z(av),
        "sentiment_label": p.sentiment_label,
        "sentiment_score": p.sentiment_score,
        "impact_score": p.impact_score,
        "currency_pairs": p.currency_pairs or [],
        "thematic_tags": p.thematic_tags or [],
        "asset_impacts": getattr(p, "asset_impacts", None) or [],
        "quant_taxonomy": getattr(p, "quant_taxonomy", None) or {},
        "summary": (p.summary or p.abstract or "")[:summary_chars] or None,
        "source_url": p.source_url,
        "pdf_url": p.pdf_url,
        "image_url": p.image_url,
        "llm_record": format_llm_record(row_dict),
    }


def _window(horizon_days, start, end):
    now = utcnow()
    hi = min(end, now) if end else now
    lo = start if start else (now - timedelta(days=int(horizon_days)) if horizon_days else None)
    return lo, hi


def query_papers(db: Session, *, horizon_days=None, start=None, end=None, categories=None,
                 zone=None, sentiment=None, pair=None, thematic_tags=None, min_importance=None,
                 source_kind=None, source=None, q=None, sort="date", skip=0, limit=50,
                 include_suspect=False):
    """Shared paper filter. Returns (rows, total_matched_or_None)."""
    lo, hi = _window(horizon_days, start, end)
    qy = db.query(models.Paper).filter(models.Paper.published_date <= hi)
    if lo:
        qy = qy.filter(models.Paper.published_date >= lo)
    if not include_suspect:
        qy = qy.filter(or_(models.Paper.published_precision.is_(None),
                           models.Paper.published_precision != "suspect"))
    names = None
    if categories:
        cats = [c for c in categories if c]
        names = {n for c in cats for n in catalog.sources_in_category(c)}
    if source_kind:
        kind_names = {s["name"] for s in catalog.CATALOG if s.get("kind") == source_kind}
        names = kind_names if names is None else names & kind_names
    if names is not None:
        if not names:
            return [], 0
        qy = qy.filter(models.Paper.source.in_(sorted(names)))
    if source:
        qy = qy.filter(models.Paper.source.ilike(f"%{source}%"))
    if sentiment:
        qy = qy.filter(models.Paper.sentiment_label == sentiment.lower())
    if min_importance is not None:
        qy = qy.filter(models.Paper.impact_score >= 10.0 * float(min_importance))
    tree = search.parse(q) if q else None
    need = search.required_terms(tree) if tree is not None else None
    if need:
        cols = (models.Paper.title, models.Paper.abstract, models.Paper.summary, models.Paper.full_text)
        qy = qy.filter(or_(*[c.ilike(f"%{t}%") for t in need for c in cols]))
    else:
        qy = qy.options(defer(models.Paper.full_text))
    order = desc(models.Paper.impact_score) if sort == "impact" else desc(models.Paper.published_date)
    qy = qy.order_by(order, desc(models.Paper.published_date))

    pairn = norm_pair(pair)
    post = bool(zone or pairn or thematic_tags or tree is not None)
    if not post:
        total = qy.count()
        return qy.offset(skip).limit(limit).all(), total
    out, seen = [], 0
    for p in qy.yield_per(500):
        if zone and not _zone_ok(p, zone):
            continue
        if pairn and pairn not in (p.currency_pairs or []):
            continue
        if thematic_tags and not any(t in (p.thematic_tags or []) for t in thematic_tags):
            continue
        if tree is not None and not search.match(tree, search.text_blob(p, with_full_text=bool(need))):
            continue
        seen += 1
        if seen > skip:
            out.append(p)
        if len(out) >= limit:
            break
    return out, None


def resolve_refs(db: Session, refs: List[str]):
    """'RP-xxxxxxxx' / 8-hex prefix / full uuid -> paper. Ambiguous prefixes are errors."""
    found, errors = {}, {}
    for r in refs:
        key = (r or "").strip()
        if not key:
            continue
        hexp = key[3:] if key.upper().startswith("RP-") else key
        hexp = hexp.lower()
        if len(hexp) == 36:
            rows = db.query(models.Paper).filter(models.Paper.id == hexp).all()
        else:
            if len(hexp) < 8:
                errors[key] = "référence trop courte (8 caractères hexadécimaux requis)"
                continue
            like = hexp[:8] + "-%" if len(hexp) == 8 else hexp + "%"
            rows = db.query(models.Paper).filter(models.Paper.id.like(like)).all()
        if not rows:
            errors[key] = "introuvable"
        elif len(rows) > 1:
            errors[key] = f"ambiguë ({len(rows)} articles) : utiliser l'id complet"
        else:
            found[key] = rows[0]
    return found, errors


def citation(p) -> dict:
    meta = catalog.META.get(p.source, {})
    b = brief(p, summary_chars=600)
    return {
        "ref": b["ref"], "paper_id": p.id, "title": p.title, "authors": p.authors,
        "institution": b["institution"], "source": p.source, "category": b["category"],
        "zone": b["zone"], "published_date": b["published_date"],
        "published_precision": p.published_precision, "available_at": b["available_at"],
        "url": p.source_url or p.pdf_url, "pdf_url": p.pdf_url, "doi": p.doi,
        "excerpt": b["summary"], "sentiment_label": p.sentiment_label,
        "sentiment_score": p.sentiment_score, "text_source": p.text_source,
        "portal": meta.get("portal"), "retrieved_at": iso_z(utcnow()),
        "markdown": f"[{b['ref']}]",
    }


def _src_health(db: Session):
    from scripts import ingest as ingest_mod
    now = utcnow()
    counts = dict(db.query(models.Paper.source, func.count(models.Paper.id)).group_by(models.Paper.source).all())
    last_dates = dict(db.query(models.Paper.source, func.max(models.Paper.published_date))
                      .filter(models.Paper.published_date <= now)
                      .group_by(models.Paper.source).all())
    errors = ingest_mod.get_source_errors()
    rows = []
    for s in catalog.CATALOG:
        last = last_dates.get(s["name"])
        if s["kind"] == "portal":
            status = "portal"            # link-only desk, never ingested: not an error
            days = None
        elif last is None:
            status, days = "error", None
        else:
            days = (now - last).days
            status = "healthy" if days <= 7 else "warning" if days <= 30 else "error"
        rows.append({"name": s["name"], "kind": s["kind"], "category": s["category"], "zone": s["zone"],
                     "last_published": iso_z(last), "days_since_last": days, "status": status,
                     "total_docs": counts.get(s["name"], 0), "last_error": errors.get(s["name"]),
                     "feed": s.get("feed"), "portal": s.get("portal")})
    order = {"error": 0, "warning": 1, "healthy": 2, "portal": 3}
    rows.sort(key=lambda x: (order.get(x["status"], 9), x["name"]))
    return rows


# ---------------------------------------------------------------------------
# capability catalogue (discovery)
# ---------------------------------------------------------------------------

class MCPMethodInfo(BaseModel):
    name: str
    method: str = "GET"
    path: str
    description: str
    parameters: Dict[str, Any]


def _cap(name, description, props, required=(), method="GET"):
    return MCPMethodInfo(name=name, method=method, path=f"/mcp/{name}", description=description,
                         parameters={"type": "object", "properties": props, "required": list(required)})


_FILTERS = {
    "horizon_days": {"type": "integer", "description": "Last N days (1-3650)."},
    "start": {"type": "string", "format": "date-time", "description": "Window start, UTC (overrides horizon_days)."},
    "end": {"type": "string", "format": "date-time", "description": "Window end, UTC (default now). Point-in-time cut."},
    "categories": {"type": "array", "items": {"type": "string"},
                   "description": "Union of: central_bank, commercial_bank, fund, multilateral, academic, macro_news."},
    "zone": {"type": "string", "description": "USD EUR GBP JPY CHF CAD AUD NZD SEK INR GLB (source zone or paper area)."},
    "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
    "pair": {"type": "string", "description": "EURUSD or EUR/USD."},
    "thematic_tags": {"type": "array", "items": {"type": "string"}},
    "min_importance": {"type": "integer", "description": "0-10: impact_score >= 10 x value."},
    "source_kind": {"type": "string", "enum": ["rss", "scrape", "article_scrape", "portal"]},
    "source": {"type": "string", "description": "Source name substring."},
    "q": {"type": "string", "description": "Boolean query: AND OR NOT ( ) \"phrase\", -term; juxtaposition = AND."},
    "sort": {"type": "string", "enum": ["date", "impact"]},
    "skip": {"type": "integer"}, "limit": {"type": "integer", "description": "<= 200"},
}

CAPABILITIES = {c.name: c for c in [
    _cap("papers_search", "Search stored research (central banks, desks, funds, multilateral, academic, news) with filters and an optional boolean query.", _FILTERS),
    _cap("search_by_keywords", "Same as papers_search with a mandatory boolean query.", _FILTERS, ["q"]),
    _cap("papers_by_asset", "Papers about an asset class (keyword match).", {"asset": {"type": "string", "enum": list(ASSET_KEYWORDS)}, **_FILTERS}, ["asset"]),
    _cap("papers_resolve", "Resolve citation refs (RP-xxxxxxxx, prefix or uuid) to papers.", {"refs": {"type": "string", "description": "Comma-separated refs."}}, ["refs"]),
    _cap("cite", "Frozen citation record of a paper (ref, institution, dates, url, excerpt) for a thesis or report.", {"paper_id": {"type": "string"}}, ["paper_id"]),
    _cap("paper_fulltext", "Full text of a paper (PDF or article body), fetched once and stored.", {"paper_id": {"type": "string"}, "max_chars": {"type": "integer"}, "refresh": {"type": "boolean"}}, ["paper_id"]),
    _cap("get_paper_by_id", "One paper with full metadata.", {"paper_id": {"type": "string"}}, ["paper_id"]),
    _cap("window_digest", "Counts and top papers of a UTC window by zone and category (report collection).", {"start": _FILTERS["start"], "end": _FILTERS["end"], "zones": {"type": "string"}, "per_zone": {"type": "integer"}}, ["start"]),
    _cap("platform_health", "Per-source freshness, document count and last ingestion error.", {"detailed": {"type": "boolean"}}),
    _cap("get_stats", "Database counts.", {"horizon_days": _FILTERS["horizon_days"]}),
    _cap("get_sources", "Provenance catalogue.", {"detailed": {"type": "boolean"}}),
    _cap("get_central_banks", "Central-bank output by monetary zone.", {"days": {"type": "integer"}, "per_zone": {"type": "integer"}}),
    _cap("get_by_institution", "Recent papers grouped by institution for a category.", {"category": {"type": "string"}, "days": {"type": "integer"}, "per": {"type": "integer"}}, ["category"]),
    _cap("fx_calls", "Directional FX stances of bank/fund notes (no levels).", {"days": {"type": "integer"}, "pair": {"type": "string"}, "category": {"type": "string"}}),
    _cap("get_board", "Percentile-calibrated EmbeddingProp sentiment by currency bloc (news only).", {"days": {"type": "integer"}, "lookback_days": {"type": "integer"}}),
    _cap("market_sentiment", "Normalized 0-100 Market Sentiment Indicator with 90d historical percentile, z-score, regime, and zone ranking (news only).", {"days": {"type": "integer"}, "lookback_days": {"type": "integer"}, "half_life_hours": {"type": "number"}}),
    _cap("semantic_search", "Hybrid 256d int8 dense + BM25 search across news or quant research papers, returning LLM-ready records.", {"q": {"type": "string"}, "track": {"type": "string", "enum": ["news", "papers"]}, "limit": {"type": "integer"}}, ["q"]),
    _cap("quant_taxonomy", "Research papers grouped by quantitative strategy families and concepts.", {"days": {"type": "integer"}, "family": {"type": "string"}, "limit": {"type": "integer"}}),
    _cap("macro_facts", "Numbers quoted in articles (prints, bank forecasts, consensus, previous, rate decisions), each with its exact quote; same zone codes as fx_calls.",
         {"zone": {"type": "string", "description": "USD EUR GBP JPY CHF CAD AUD NZD CNY SEK NOK INR MXN"},
          "indicator": {"type": "string", "enum": list(macrofacts.INDICATORS)},
          "kind": {"type": "string", "enum": list(macrofacts.KINDS)},
          "geo": {"type": "string", "description": "US EA DE FR ES IT NL BE UK JP JP-TKY CH CA AU NZ CN SE NO IN MX"},
          "days": {"type": "integer", "description": "1-730, default 30"},
          "paper_id": {"type": "string"}, "limit": {"type": "integer", "description": "<= 2000"}}),
    _cap("macro_board", "Per zone/geo/indicator: last actual, consensus, actual - consensus, forecasts (median, dispersion = max - min, institutions); every value quoted; method in the response.",
         {"days": {"type": "integer", "description": "1-365, default 14"},
          "zones": {"type": "string", "description": "Comma-separated, e.g. USD,EUR,GBP,JPY"}}),
    _cap("get_events", "Upcoming high-impact events (ForexFactory).", {"limit": {"type": "integer"}, "include_medium": {"type": "boolean"}}),
    _cap("quant_search", "arXiv q-fin search.", {"q": {"type": "string"}, "asset": {"type": "string"}, "strategy": {"type": "string"}, "sort": {"type": "string"}, "limit": {"type": "integer"}}),
    _cap("get_digest", "Net sentiment over a list of paper ids.", {"ids": {"type": "array", "items": {"type": "string"}}}, ["ids"], method="POST"),
    _cap("link_thesis", "Tag a paper with a Horacle thesis id (backlink).", {"paper_id": {"type": "string"}, "thesis_id": {"type": "string"}}, ["paper_id", "thesis_id"], method="POST"),
    _cap("ingest_trigger", "Start one ingestion pass (no-op if one is running).", {"analyze": {"type": "boolean"}}, method="POST"),
]}


@router.get("/.well-known/mcp-capabilities", include_in_schema=False)
def mcp_capabilities():
    return CAPABILITIES


@router.get("/mcp/status")
def mcp_status():
    from scripts import ingest as ingest_mod
    return {"status": "ok", "now_utc": iso_z(utcnow()), "version": "2.0.0",
            "ingest_running": ingest_mod.is_running(), "last_ingest": ingest_mod.last_status() or None,
            "capabilities": sorted(CAPABILITIES)}


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

def _search_endpoint(db, *, q_required=False, **kw):
    if q_required and not (kw.get("q") or "").strip():
        raise HTTPException(400, "q is required")
    rows, total = query_papers(db, **kw)
    return {"count": len(rows), "total": total, "now_utc": iso_z(utcnow()),
            "papers": [brief(p) for p in rows],
            "filters_applied": {k: (v.isoformat() if isinstance(v, datetime) else v)
                                for k, v in kw.items() if v not in (None, [], "")}}


def _utc(d: Optional[datetime]) -> Optional[datetime]:
    from datetime import timezone as _tz
    return d.astimezone(_tz.utc).replace(tzinfo=None) if d is not None and d.tzinfo else d


def _filter_params(
    horizon_days: Optional[int] = Query(None, ge=1, le=3650),
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    categories: Optional[List[str]] = Query(None),
    zone: Optional[str] = None,
    sentiment: Optional[str] = None,
    pair: Optional[str] = None,
    thematic_tags: Optional[List[str]] = Query(None),
    min_importance: Optional[int] = Query(None, ge=0, le=10),
    source_kind: Optional[str] = None,
    source: Optional[str] = None,
    q: Optional[str] = None,
    sort: str = Query("date", pattern="^(date|impact)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    start, end = _utc(start), _utc(end)
    return dict(horizon_days=horizon_days, start=start, end=end, categories=categories,
                zone=zone.upper() if zone else None, sentiment=sentiment, pair=pair,
                thematic_tags=thematic_tags, min_importance=min_importance,
                source_kind=source_kind, source=source, q=q, sort=sort, skip=skip, limit=limit)


@router.get("/mcp/papers_search")
@router.get("/mcp/papers/search", include_in_schema=False)
def mcp_papers_search(f: dict = Depends(_filter_params), db: Session = Depends(get_db)):
    return _search_endpoint(db, **f)


@router.get("/mcp/search_by_keywords")
def mcp_search_by_keywords(f: dict = Depends(_filter_params), db: Session = Depends(get_db)):
    return _search_endpoint(db, q_required=True, **f)


@router.get("/mcp/papers_by_asset")
@router.get("/mcp/papers/by-asset", include_in_schema=False)
def mcp_papers_by_asset(asset: str = Query(...), f: dict = Depends(_filter_params),
                        db: Session = Depends(get_db)):
    kws = ASSET_KEYWORDS.get(asset)
    if not kws:
        raise HTTPException(400, f"asset must be one of {list(ASSET_KEYWORDS)}")
    expr = " OR ".join(f'"{k.strip()}"' for k in kws)
    f["q"] = f"({expr}) AND ({f['q']})" if f.get("q") else expr
    out = _search_endpoint(db, **f)
    out["asset"] = asset
    return out


@router.get("/mcp/papers_resolve")
def mcp_papers_resolve(refs: str = Query(...), db: Session = Depends(get_db)):
    found, errors = resolve_refs(db, [r for r in refs.split(",")])
    return {"found": {k: citation(p) for k, p in found.items()}, "errors": errors}


@router.get("/mcp/cite")
def mcp_cite(paper_id: str = Query(...), db: Session = Depends(get_db)):
    found, errors = resolve_refs(db, [paper_id])
    if not found:
        raise HTTPException(404, errors.get(paper_id, "introuvable"))
    return citation(next(iter(found.values())))


@router.get("/mcp/paper_fulltext")
def mcp_paper_fulltext(paper_id: str = Query(...), max_chars: int = Query(20000, ge=500, le=200000),
                       refresh: bool = False, db: Session = Depends(get_db)):
    found, errors = resolve_refs(db, [paper_id])
    if not found:
        raise HTTPException(404, errors.get(paper_id, "introuvable"))
    p = next(iter(found.values()))
    text, src = fulltext.ensure(p, refresh=refresh)
    db.commit()
    return {"ref": ref_of(p.id), "paper_id": p.id, "title": p.title, "text_source": src,
            "chars": len(text), "truncated": len(text) > max_chars, "text": text[:max_chars],
            "url": p.source_url or p.pdf_url, "fetched_at": iso_z(p.text_fetched_at)}


@router.get("/mcp/get_paper_by_id")
def mcp_get_paper_by_id(paper_id: str = Query(...), db: Session = Depends(get_db)):
    found, errors = resolve_refs(db, [paper_id])
    if not found:
        raise HTTPException(404, errors.get(paper_id, "introuvable"))
    p = next(iter(found.values()))
    d = brief(p, summary_chars=4000)
    d.update({"authors": p.authors, "abstract": p.abstract, "keywords": p.keywords or [],
              "sentiment_detail": p.sentiment_detail or {}, "interest_score": p.interest_score,
              "doi": p.doi, "created_at": iso_z(p.created_at), "analyzed_at": iso_z(p.analyzed_at),
              "thesis": p.thesis, "thesis_tags": p.thesis_tags or [], "text_source": p.text_source,
              "has_full_text": bool(p.full_text and p.text_source in ("pdf", "html"))})
    return d


@router.get("/mcp/window_digest")
def mcp_window_digest(start: datetime = Query(...), end: Optional[datetime] = None,
                      zones: str = "USD,EUR,GBP,JPY,CHF,CAD,AUD,NZD", per_zone: int = Query(8, ge=1, le=40),
                      db: Session = Depends(get_db)):
    lo, hi = _utc(start), min(_utc(end), utcnow()) if end else utcnow()
    rows = (db.query(models.Paper).options(defer(models.Paper.full_text))
            .filter(models.Paper.published_date >= lo, models.Paper.published_date <= hi)
            .filter(or_(models.Paper.published_precision.is_(None), models.Paper.published_precision != "suspect"))
            .order_by(desc(models.Paper.published_date)).all())
    zl = [z.strip().upper() for z in zones.split(",") if z.strip()]
    by_cat: Dict[str, int] = {}
    for p in rows:
        c = catalog.category_of(p.source)
        by_cat[c] = by_cat.get(c, 0) + 1
    out_z = {}
    for z in zl:
        zp = [p for p in rows if _zone_ok(p, z)]
        cb = [p for p in zp if catalog.category_of(p.source) == "central_bank" and catalog.zone_of(p.source) == z]
        scored = [p.sentiment_score for p in zp if p.sentiment_score is not None]
        top = sorted(zp, key=lambda p: (p.impact_score or 0), reverse=True)[:per_zone]
        out_z[z] = {"count": len(zp), "central_bank_count": len(cb),
                    "mean_sentiment": round(sum(scored) / len(scored), 4) if scored else None,
                    "central_bank": [brief(p, summary_chars=300) for p in cb[:per_zone]],
                    "top_impact": [brief(p, summary_chars=300) for p in top]}
    return {"window": {"start": iso_z(lo), "end": iso_z(hi)}, "total": len(rows),
            "by_category": by_cat, "zones": out_z, "now_utc": iso_z(utcnow())}


@router.get("/mcp/platform_health")
@router.get("/mcp/platform/health", include_in_schema=False)
def mcp_platform_health(detailed: bool = True, db: Session = Depends(get_db)):
    rows = _src_health(db)
    ingested = [r for r in rows if r["status"] != "portal"]
    n_err = sum(r["status"] == "error" for r in ingested)
    n_warn = sum(r["status"] == "warning" for r in ingested)
    overall = "error" if n_err > len(ingested) * 0.25 else "degraded" if (n_err or n_warn) else "healthy"
    zs: Dict[str, Dict[str, int]] = {}
    for r in rows:
        zs.setdefault(r["zone"], {}).setdefault(r["status"], 0)
        zs[r["zone"]][r["status"]] += 1
    return {"overall": overall, "now_utc": iso_z(utcnow()),
            "counts": {"ingested_sources": len(ingested), "error": n_err, "warning": n_warn,
                       "portal": len(rows) - len(ingested)},
            "zones_summary": zs,
            "sources": rows if detailed else [{"name": r["name"], "status": r["status"]} for r in rows]}


@router.get("/mcp/get_stats")
def mcp_get_stats(horizon_days: Optional[int] = Query(None, ge=1, le=3650), db: Session = Depends(get_db)):
    now = utcnow()
    valid = models.Paper.published_date <= now
    base = db.query(func.count(models.Paper.id)).filter(valid)
    cut = now - timedelta(days=horizon_days or 7)
    return {
        "total": base.scalar() or 0,
        "analyzed": base.filter(models.Paper.analyzed_at.isnot(None)).scalar() or 0,
        "with_full_text": db.query(func.count(models.Paper.id)).filter(models.Paper.text_source.in_(["pdf", "html"])).scalar() or 0,
        "window_days": horizon_days or 7,
        "in_window": db.query(func.count(models.Paper.id)).filter(valid, models.Paper.published_date >= cut).scalar() or 0,
        "sentiment": dict(db.query(models.Paper.sentiment_label, func.count(models.Paper.id))
                          .filter(valid, models.Paper.sentiment_label.isnot(None))
                          .group_by(models.Paper.sentiment_label).all()),
        "precision": dict(db.query(models.Paper.published_precision, func.count(models.Paper.id))
                          .group_by(models.Paper.published_precision).all()),
        "sources": dict(db.query(models.Paper.source, func.count(models.Paper.id)).filter(valid)
                        .group_by(models.Paper.source).all()),
        "oldest": iso_z(db.query(func.min(models.Paper.published_date)).scalar()),
        "newest": iso_z(db.query(func.max(models.Paper.published_date)).filter(valid).scalar()),
        "now_utc": iso_z(now),
    }


@router.get("/mcp/get_sources")
def mcp_get_sources(detailed: bool = True, db: Session = Depends(get_db)):
    rows = _src_health(db)
    if not detailed:
        rows = [{k: r[k] for k in ("name", "kind", "zone", "total_docs", "status")} for r in rows]
    return {"categories": catalog.CATEGORIES, "zones": catalog.ZONES, "count": len(rows), "sources": rows}


@router.get("/mcp/get_central_banks")
def mcp_get_central_banks(days: int = Query(30, ge=1, le=365), per_zone: int = Query(6, ge=1, le=30),
                          db: Session = Depends(get_db)):
    now = utcnow()
    cb = catalog.sources_in_category("central_bank")
    rows = (db.query(models.Paper).options(defer(models.Paper.full_text))
            .filter(models.Paper.source.in_(cb), models.Paper.published_date <= now,
                    models.Paper.published_date >= now - timedelta(days=days))
            .order_by(desc(models.Paper.published_date)).all())
    out = []
    for code in catalog.ZONE_ORDER:
        meta = catalog.ZONES.get(code)
        srcs = [s for s in catalog.CATALOG if s["zone"] == code and s["category"] == "central_bank"]
        if not meta or not srcs:
            continue
        ps = [p for p in rows if catalog.zone_of(p.source) == code]
        sc = [p.sentiment_score for p in ps if p.sentiment_score is not None]
        out.append({"zone": code, "authority": meta["authority"], "count": len(ps),
                    "net_score": round(sum(sc) / len(sc), 4) if sc else None,
                    "institutions": sorted({s["institution"] for s in srcs}),
                    "latest": [brief(p, summary_chars=300) for p in ps[:per_zone]]})
    return {"window_days": days, "now_utc": iso_z(now), "zones": out}


@router.get("/mcp/get_by_institution")
def mcp_get_by_institution(category: str = Query(...), days: int = Query(180, ge=1, le=730),
                           per: int = Query(8, ge=1, le=40), db: Session = Depends(get_db)):
    now = utcnow()
    rows = (db.query(models.Paper).options(defer(models.Paper.full_text))
            .filter(models.Paper.source.in_(catalog.sources_in_category(category)),
                    models.Paper.published_date <= now,
                    models.Paper.published_date >= now - timedelta(days=days))
            .order_by(desc(models.Paper.published_date)).all())
    groups: Dict[str, list] = {}
    for p in rows:
        groups.setdefault(catalog.META.get(p.source, {}).get("institution", p.source), []).append(p)
    out = []
    for inst, ps in groups.items():
        sc = [p.sentiment_score for p in ps if p.sentiment_score is not None]
        out.append({"institution": inst, "zone": catalog.zone_of(ps[0].source), "count": len(ps),
                    "net_score": round(sum(sc) / len(sc), 4) if sc else None,
                    "latest": [brief(p, summary_chars=300) for p in ps[:per]]})
    out.sort(key=lambda g: g["count"], reverse=True)
    return {"category": category, "window_days": days, "groups": out}


@router.get("/mcp/fx_calls")
@router.get("/mcp/fx/calls", include_in_schema=False)
@router.get("/mcp/get_transactions", include_in_schema=False)
def mcp_fx_calls(days: int = Query(120, ge=1, le=730), pair: Optional[str] = None,
                 category: Optional[str] = Query(None, description="commercial_bank | fund (default both)"),
                 limit: int = Query(200, ge=1, le=500), db: Session = Depends(get_db)):
    now = utcnow()
    cats = [category] if category else ["commercial_bank", "fund"]
    names = [s for c in cats for s in catalog.sources_in_category(c)]
    want = (pair or "").upper().replace("/", "") or None
    rows = (db.query(models.Paper).options(defer(models.Paper.full_text))
            .filter(models.Paper.source.in_(names), models.Paper.published_date <= now,
                    models.Paper.published_date >= now - timedelta(days=days))
            .order_by(desc(models.Paper.published_date)).all())
    calls = []
    for p in rows:
        for c in fxcalls.extract_calls(p):
            if want and c["pair"].replace("/", "") != want:
                continue
            calls.append({"ref": ref_of(p.id), "paper_id": p.id,
                          "institution": catalog.META.get(p.source, {}).get("institution", p.source),
                          "source": p.source, "date": iso_z(p.published_date), "pair": c["pair"],
                          "bias": c["bias"], "bias_inferred": True, "sentiment_score": p.sentiment_score, "thesis": p.title,
                          "url": p.source_url or p.pdf_url})
            if len(calls) >= limit:
                break
        if len(calls) >= limit:
            break
    return {"window_days": days, "pair": pair, "count": len(calls),
            "bias_basis": ("inferred: bullish/bearish cue words in title + abstract, EmbeddingProp label "
                           "as fallback; not the desk's stated recommendation - read the note"),
            "calls": calls}


@router.get("/mcp/market_sentiment")
def mcp_market_sentiment(
    days: int = Query(7, ge=1, le=90),
    lookback_days: int = Query(90, ge=14, le=365),
    half_life_hours: float = Query(72.0, ge=6.0, le=360.0),
    db: Session = Depends(get_db),
):
    return build_market_sentiment(
        db, days=days, lookback_days=lookback_days, half_life_hours=half_life_hours
    )


@router.get("/mcp/semantic_search")
def mcp_semantic_search(
    q: str = Query(..., min_length=2),
    track: str = Query("news", pattern="^(news|papers)$"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    svc = embedding_engine.get_service()
    hits = svc.semantic_search(q, track=track, top_k=limit, db=db)
    ids = [h["doc_id"] for h in hits]
    rows_by_id = (
        {str(p.id): p for p in db.query(models.Paper).filter(models.Paper.id.in_(ids)).all()}
        if ids
        else {}
    )
    results = []
    for h in hits:
        p = rows_by_id.get(h["doc_id"])
        if p is None:
            continue
        b = brief(p, summary_chars=500)
        results.append(
            {
                "similarity_score": h["score"],
                "dense_score": h["dense_score"],
                "bm25_score": h["bm25_score"],
                "matched_chunk_type": h["matched_chunk_type"],
                "matched_excerpt": h["matched_excerpt"],
                "record": b["llm_record"],
            }
        )
    return {"query": q, "track": track, "count": len(results), "results": results}


@router.get("/mcp/quant_taxonomy")
def mcp_quant_taxonomy(
    days: int = Query(30, ge=1, le=365),
    family: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    now = utcnow()
    cutoff = now - timedelta(days=days)
    rows = (
        db.query(models.Paper)
        .options(defer(models.Paper.full_text))
        .filter(models.Paper.published_date >= cutoff, models.Paper.published_date <= now)
        .filter(or_(models.Paper.analysis_tier.is_(None), models.Paper.analysis_tier != "noise_filtered"))
        .order_by(
            desc(models.Paper.doc_kind == "paper"),
            desc(models.Paper.published_date),
        )
        .limit(max(limit * 20, 400))
        .all()
    )
    papers = [p for p in rows if _infer_doc_kind(p) == "paper"]
    papers.sort(key=lambda p: (getattr(p, "analysis_engine", None) == "embedding_prop_v3", p.impact_score or 0.0), reverse=True)
    family_counts: Dict[str, int] = {}
    concept_counts: Dict[str, int] = {}
    items = []
    for p in papers:
        qt = getattr(p, "quant_taxonomy", None) or {}
        fams = [f.get("family") for f in (qt.get("strategy_families") or []) if isinstance(f, dict) and f.get("family")]
        if family and not any(family.lower() in f.lower() for f in fams):
            continue
        if fams:
            family_counts[fams[0]] = family_counts.get(fams[0], 0) + 1
        for c in (qt.get("quant_concepts") or []):
            concept_counts[c] = concept_counts.get(c, 0) + 1
        items.append(brief(p, summary_chars=400)["llm_record"])
        if len(items) >= limit:
            break
    return {
        "window_days": days,
        "count": len(items),
        "by_family": sorted(family_counts.items(), key=lambda x: x[1], reverse=True),
        "top_concepts": sorted(concept_counts.items(), key=lambda x: x[1], reverse=True)[:12],
        "papers": items,
    }


@router.get("/mcp/get_board")
def mcp_get_board(
    days: int = Query(7, ge=1, le=365),
    lookback_days: int = Query(90, ge=14, le=365),
    db: Session = Depends(get_db),
):
    ind = build_market_sentiment(db, days=days, lookback_days=lookback_days)
    region_map = dict(CURRENCY_BLOCS)
    blocs = [
        {
            "bloc": z["zone"],
            "region": region_map.get(z["zone"], z["zone"]),
            "count": z["doc_count"],
            "score": z["raw_score"],
            "normalized_0_100": z["normalized_0_100"],
            "historical_percentile": z["historical_percentile"],
            "z_score": z["z_score"],
            "regime": z["regime"],
            "relative_rank": z["relative_rank"],
            "positive": z["positive_count"],
            "negative": z["negative_count"],
            "neutral": z["neutral_count"],
        }
        for z in ind["zones"]
    ]
    return {
        "window_days": days,
        "now_utc": ind["as_of_utc"],
        "global_indicator": {
            "raw_score": ind["raw_score"],
            "normalized_0_100": ind["normalized_0_100"],
            "historical_percentile": ind["historical_percentile"],
            "z_score": ind["z_score"],
            "regime": ind["regime"],
            "calibration_status": ind["calibration_status"],
        },
        "blocs": blocs,
        "note": ind["methodology"],
    }


@router.get("/mcp/get_events")
def mcp_get_events(limit: int = Query(12, ge=1, le=80), include_medium: bool = False,
                   db: Session = Depends(get_db)):
    rows, err = ev.upcoming(limit=limit, impacts=("High", "Medium") if include_medium else ("High",))
    return {"source": "ForexFactory weekly feed", "error": err, "events": rows}


@router.get("/mcp/macro_facts")
def mcp_macro_facts(zone: Optional[str] = None, indicator: Optional[str] = None,
                    kind: Optional[str] = Query(None, pattern="^(actual|forecast|consensus|previous|decision)$"),
                    geo: Optional[str] = None, days: int = Query(30, ge=1, le=730),
                    paper_id: Optional[str] = None, limit: int = Query(200, ge=1, le=2000),
                    db: Session = Depends(get_db)):
    if indicator and indicator not in macrofacts.INDICATORS:
        raise HTTPException(status_code=422, detail=f"unknown indicator; one of {sorted(macrofacts.INDICATORS)}")
    facts = macrofacts.query_facts(db, zone=zone, indicator=indicator, kind=kind, geo=geo, days=days,
                                   paper_id=paper_id, limit=limit)
    return {"window_days": days, "count": len(facts),
            "rule": "quote is an exact excerpt of the article and contains value; nothing is derived",
            "facts": facts}


@router.get("/mcp/macro_board")
def mcp_macro_board(days: int = Query(14, ge=1, le=365), zones: Optional[str] = None,
                    db: Session = Depends(get_db)):
    zs = [z.strip() for z in zones.split(",") if z.strip()] if zones else None
    return macrofacts.build_board(db, days=days, zones=zs)


@router.get("/mcp/quant_search")
@router.get("/mcp/quant/search", include_in_schema=False)
def mcp_quant_search(q: str = "", asset: Optional[str] = None, strategy: Optional[str] = None,
                     sort: str = "submittedDate", limit: int = Query(40, ge=1, le=100)):
    from . import quant as qmod
    assets = [asset] if asset in qmod.ASSETS else None
    strategies = [strategy] if strategy in qmod.STRATEGIES else None
    cats = None if (q or assets or strategies) else ["q-fin.TR"]
    papers = qmod.search(q=q, cats=cats, assets=assets, strategies=strategies, max_results=limit, sort=sort)
    return {"count": len(papers), "query": qmod.build_query(q, cats, assets, strategies), "papers": papers}


@router.post("/mcp/get_digest")
def mcp_get_digest(req: schemas.DigestRequest, db: Session = Depends(get_db)):
    found, errors = resolve_refs(db, req.ids)
    ps = list(found.values())
    sc = [p for p in ps if p.sentiment_score is not None]
    net = round(sum(p.sentiment_score for p in sc) / len(sc), 4) if sc else 0.0
    ranked = sorted(sc, key=lambda p: p.sentiment_score, reverse=True)
    return {"count": len(ps), "analyzed": len(sc), "net_score": net,
            "net_label": "positive" if net > 0.03 else "negative" if net < -0.03 else "neutral",
            "top_positive": [brief(p, summary_chars=200) for p in ranked[:3]],
            "top_negative": [brief(p, summary_chars=200) for p in ranked[-3:][::-1]],
            "errors": errors}


class ThesisLink(BaseModel):
    paper_id: str
    thesis_id: str


@router.post("/mcp/link_thesis")
def mcp_link_thesis(req: ThesisLink, db: Session = Depends(get_db)):
    found, errors = resolve_refs(db, [req.paper_id])
    if not found:
        raise HTTPException(404, errors.get(req.paper_id, "introuvable"))
    p = next(iter(found.values()))
    tags = list(p.thesis_tags or [])
    if req.thesis_id not in tags:
        tags.append(req.thesis_id)
        p.thesis_tags = tags
        db.commit()
    return {"ref": ref_of(p.id), "thesis_tags": tags}


@router.post("/mcp/ingest_trigger")
@router.post("/mcp/ingest/trigger", include_in_schema=False)
def mcp_ingest_trigger(analyze: bool = True):
    from scripts import ingest as ingest_mod
    if ingest_mod.is_running():
        return {"status": "already_running", "now_utc": iso_z(utcnow())}

    def _job():
        try:
            ingest_mod.run(do_analyze=analyze, days=7)
        except Exception as e:
            print(f"[!] MCP ingest job error: {e}")
    threading.Thread(target=_job, daemon=True).start()
    return {"status": "started", "analyze": analyze, "now_utc": iso_z(utcnow())}
