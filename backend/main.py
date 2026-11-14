from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, or_, and_, not_
from typing import List, Optional
from datetime import datetime, timedelta
import os
import re
import time
import threading
import requests
from fastapi.responses import StreamingResponse, Response

from . import models, schemas, analysis, reports, fxcalls, pdfscrape, quant
from . import sources as catalog
from . import search as search_mod
from . import netguard
from . import events as events_mod
from . import mcp as mcp_mod
from .timeutil import utcnow
from sqlalchemy.orm import defer
from starlette.responses import JSONResponse
from .database import SessionLocal, engine, get_db, run_migrations
from scripts import ingest as ingest_mod  # noqa: WPS433

# Create / migrate DB tables
models.Base.metadata.create_all(bind=engine)
run_migrations()

app = FastAPI(title="Macroeconomic Research Aggregator API")

# --- MCP surface for LLM integration (backend/mcp.py, one router) ---
app.include_router(mcp_mod.router)

# Local-only service: the browser UI is served on :3001. `allow_origins=["*"]`
# with credentials let any web page the user visits drive the API (ingest,
# watchlist overwrite, thesis tags). Only the local UI origins are allowed, and
# state-changing requests carrying a foreign Origin header are refused (CORS
# alone does not stop a "simple" cross-site POST from being executed).
_UI_PORT = os.environ.get("WEB_PORT", "3001")
ALLOWED_ORIGINS = [f"http://localhost:{_UI_PORT}", f"http://127.0.0.1:{_UI_PORT}"] + [
    o.strip() for o in os.environ.get("RP_EXTRA_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def _origin_guard(request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"detail": f"origin {origin} refused"}, status_code=403)
    return await call_next(request)

# ---------------------------------------------------------------------------
# Scheduler automatique d'ingestion (toutes les 30 min par défaut, avec retry)
# ---------------------------------------------------------------------------
SYNC_INTERVAL_SEC = 30 * 60          # 30 minutes : bon compromis réactivité / ressources
SYNC_MAX_RETRIES = 3                 # tentatives en cas d'échec
SYNC_RETRY_BACKOFF_SEC = 60          # attente entre deux tentatives


def _sync_once():
    """Une passe d'ingestion dans le contexte applicatif."""
    try:
        import sys, os
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
        from scripts import ingest as ingest_mod  # noqa: WPS433
        models.Base.metadata.create_all(bind=engine)
        run_migrations()
        db = SessionLocal()
        try:
            ingest_mod.run(do_analyze=True, days=7)
        finally:
            db.close()
    except Exception as e:
        print(f"[!] auto-sync error: {e}")


def _sync_with_retry():
    last_err = None
    for attempt in range(1, SYNC_MAX_RETRIES + 1):
        try:
            _sync_once()
            print(f"[*] auto-sync OK (tentative {attempt})")
            return
        except Exception as e:
            last_err = e
            wait = SYNC_RETRY_BACKOFF_SEC * attempt
            print(f"[!] auto-sync tentative {attempt} échouée: {e} — retry dans {wait}s")
            time.sleep(wait)
    print(f"[!] auto-sync échoué après {SYNC_MAX_RETRIES} tentatives: {last_err}")


def _scheduler_loop():
    print(f"[*] scheduler d'ingestion démarré (intervalle={SYNC_INTERVAL_SEC//60} min)")
    # Première passe immédiatement au démarrage
    _sync_with_retry()
    while True:
        time.sleep(SYNC_INTERVAL_SEC)
        _sync_with_retry()


@app.on_event("startup")
def _start_scheduler():
    # One scheduler only. The launchd agent io.optiwealth.research.ingest
    # (automate_platform.py) is the default; the in-process loop ran in
    # parallel with it before 2026-11-13. Opt in with RP_INAPP_SCHEDULER=1.
    if os.environ.get("RP_INAPP_SCHEDULER", "0") == "1":
        threading.Thread(target=_scheduler_loop, daemon=True).start()


PDF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
}


@app.get("/api/papers", response_model=List[schemas.PaperResponse])
def get_papers(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, le=200),
    horizon_days: Optional[int] = Query(None),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    thematic_tags: Optional[List[str]] = Query(None),
    country_tags: Optional[List[str]] = Query(None),
    source: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    zone: Optional[str] = Query(None),
    sentiment: Optional[str] = Query(None),
    pair: Optional[str] = Query(None),
    author: Optional[str] = Query(None),
    doi: Optional[str] = Query(None),
    domain: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(models.Paper).options(defer(models.Paper.full_text))

    # Drop bad/future-dated rows: feeds sometimes emit garbage dates (e.g. 2035)
    # because the real date lives in a field the parser missed.
    query = query.filter(models.Paper.published_date <= utcnow())

    if horizon_days:
        cutoff = utcnow() - timedelta(days=int(horizon_days))
        query = query.filter(models.Paper.published_date >= cutoff)
    else:
        if start_date:
            query = query.filter(models.Paper.published_date >= start_date)
        if end_date:
            query = query.filter(models.Paper.published_date <= end_date)

    if source:
        query = query.filter(models.Paper.source.ilike(f"%{source}%"))
    if category:
        names = catalog.sources_in_category(category)
        query = query.filter(models.Paper.source.in_(names)) if names else query.filter(False)
    zone_u = zone.upper() if zone else None
    if sentiment:
        query = query.filter(models.Paper.sentiment_label == sentiment.lower())
    if author:
        query = query.filter(models.Paper.authors.ilike(f"%{author}%"))
    if doi:
        query = query.filter(models.Paper.doi.ilike(f"%{doi}%"))
    if domain:
        query = query.filter(models.Paper.source.ilike(f"%{domain}%"))

    papers = query.order_by(desc(models.Paper.published_date)).all()

    # JSON-array tag filtering + recherche plein texte booléenne (Python, SQLite-friendly)
    tree = search_mod.parse(search) if search else None
    pair_n = mcp_mod.norm_pair(pair)
    out = []
    for p in papers:
        if zone_u and not mcp_mod._zone_ok(p, zone_u):
            continue
        if thematic_tags and not any(t in (p.thematic_tags or []) for t in thematic_tags):
            continue
        if country_tags and not any(t in (p.country_tags or []) for t in country_tags):
            continue
        if pair_n and pair_n not in (p.currency_pairs or []):
            continue
        if tree is not None and not search_mod.match(tree, search_mod.text_blob(p, with_full_text=False)):
            continue
        out.append(p)

    return out[skip:skip + limit]


@app.get("/api/stats", response_model=schemas.StatsResponse)
def get_stats(db: Session = Depends(get_db)):
    now = utcnow()
    valid = models.Paper.published_date <= now
    total = db.query(func.count(models.Paper.id)).filter(valid).scalar() or 0
    analyzed = (
        db.query(func.count(models.Paper.id))
        .filter(valid)
        .filter(models.Paper.analyzed_at.isnot(None))
        .scalar()
        or 0
    )
    week_cutoff = now - timedelta(days=7)
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
        total=total, analyzed=analyzed, sentiment=sentiment,
        sources=sources, last_week=last_week,
    )





@app.get("/api/compare", response_model=schemas.CompareResponse)
def get_compare(
    days: int = Query(30, ge=1, le=365),
    zones: str = Query("US,EU", description="Zones séparées par des virgules, ex: US,EU"),
    db: Session = Depends(get_db),
):
    """Compare le ton/biais (FinBERT) par source sur une fenêtre de temps,
    filtré par zone monétaire (ex: US et EU)."""
    now = utcnow()
    cutoff = now - timedelta(days=int(days))
    zone_list = [z.strip().upper() for z in zones.split(",") if z.strip()]
    if not zone_list:
        zone_list = ["US", "EU"]

    rows = (
        db.query(models.Paper)
        .filter(models.Paper.published_date >= cutoff)
        .filter(models.Paper.published_date <= now)
        .all()
    )

    def in_zones(p):
        tags = p.country_tags or []
        return any(z in tags for z in zone_list)

    by_source = {}
    for p in rows:
        if not in_zones(p):
            continue
        src = p.source or "Unknown"
        meta = catalog.META.get(src, {})
        g = by_source.setdefault(
            src,
            {
                "source": src,
                "institution": meta.get("institution", src),
                "zone": meta.get("zone", "GLB"),
                "count": 0,
                "analyzed": 0,
                "sentiment_sum": 0.0,
                "positive": 0,
                "negative": 0,
                "neutral": 0,
            },
        )
        g["count"] += 1
        if p.sentiment_score is not None:
            g["analyzed"] += 1
            g["sentiment_sum"] += p.sentiment_score
            lbl = (p.sentiment_label or "neutral").lower()
            if lbl == "positive":
                g["positive"] += 1
            elif lbl == "negative":
                g["negative"] += 1
            else:
                g["neutral"] += 1

    out = []
    for src, g in by_source.items():
        analyzed = g["analyzed"] or 1
        avg = round(g["sentiment_sum"] / analyzed, 4) if g["analyzed"] else 0.0
        net = round((g["positive"] - g["negative"]) / analyzed, 4) if g["analyzed"] else 0.0
        out.append(
            schemas.SourceCompare(
                source=g["source"],
                institution=g["institution"],
                zone=g["zone"],
                count=g["count"],
                analyzed=g["analyzed"],
                avg_sentiment=avg,
                positive=g["positive"],
                negative=g["negative"],
                neutral=g["neutral"],
                net_score=net,
            )
        )
    out.sort(key=lambda x: x.net_score, reverse=True)
    return schemas.CompareResponse(window_days=int(days), zones=zone_list, sources=out)

@app.get("/api/sources", response_model=schemas.SourceCatalogResponse)
def get_sources(db: Session = Depends(get_db)):
    """The provenance map: every desk/institution we aggregate, what kind it is,
    which monetary zone it speaks to, its portal, and how many docs we hold.
    Mirrors DataTradingPro's `_source`/`url` transparency."""
    counts = dict(
        db.query(models.Paper.source, func.count(models.Paper.id))
        .group_by(models.Paper.source).all()
    )
    items = [
        schemas.SourceItem(
            name=s["name"], institution=s["institution"], domain=s["domain"],
            kind=s["kind"], category=s["category"], zone=s["zone"],
            portal=s["portal"], feed=s["feed"], count=counts.get(s["name"], 0),
        )
        for s in catalog.CATALOG
    ]
    return schemas.SourceCatalogResponse(
        categories=catalog.CATEGORIES, zones=catalog.ZONES, sources=items,
    )


@app.get("/api/health", response_model=schemas.HealthResponse)
def get_health(db: Session = Depends(get_db)):
    """Indicateur de santé des sources RSS : indique pour chaque source la date
    de son dernier article et son statut (healthy / warning / error)."""
    now = utcnow()
    counts = dict(
        db.query(models.Paper.source, func.count(models.Paper.id))
        .group_by(models.Paper.source).all()
    )
    last_dates = dict(
        db.query(models.Paper.source, func.max(models.Paper.published_date))
        .group_by(models.Paper.source).all()
    )

    # Récupérer les dernières erreurs d'ingestion par source
    source_errors = ingest_mod.get_source_errors()

    sources_health = []
    degraded_count = 0
    error_count = 0

    for s in catalog.CATALOG:
        name = s["name"]
        kind = s.get("kind", "unknown")
        zone = s.get("zone", "GLB")
        total_docs = counts.get(name, 0)
        last_pub = last_dates.get(name)
        last_error = source_errors.get(name)

        if kind == "portal":
            # link-only desk (login-walled / JS): never ingested, not a failure
            status = "portal"
            days_since = None
            last_iso = last_pub.isoformat() if last_pub is not None else None
        elif last_pub is None:
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

        sources_health.append(
            schemas.SourceHealth(
                name=name,
                kind=kind,
                zone=zone,
                last_published=last_iso,
                days_since_last=days_since,
                status=status,
                total_docs=total_docs,
                last_error=last_error,
            )
        )

    # Tri : erreurs en premier, puis warnings, puis healthy par nom
    status_order = {"error": 0, "warning": 1, "healthy": 2, "portal": 3}
    sources_health.sort(key=lambda x: (status_order.get(x.status, 9), x.name))

    if error_count > 0:
        overall = "error"
    elif degraded_count > 0:
        overall = "degraded"
    else:
        overall = "healthy"

    return schemas.HealthResponse(overall=overall, sources=sources_health)


@app.get("/api/central-banks", response_model=schemas.CentralBankResponse)
def get_central_banks(
    days: int = Query(30, ge=1, le=365),
    per_zone: int = Query(6, ge=1, le=30),
    db: Session = Depends(get_db),
):
    """Recent central-bank output grouped by monetary zone (press releases,
    speeches, publications). Powers the Central Banks view."""
    now = utcnow()
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
        # only emit zones that actually host central-bank sources
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


@app.get("/api/by-institution", response_model=schemas.ByInstitutionResponse)
def get_by_institution(
    category: str = Query(..., description="commercial_bank | fund | ..."),
    days: int = Query(180, ge=1, le=730),
    per: int = Query(8, ge=1, le=40),
    db: Session = Depends(get_db),
):
    """Recent articles for a category, grouped by institution and sorted by
    publication date. Powers the Banks and Funds column views."""
    now = utcnow()
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
    # group by institution (one institution may span several sources)
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
            institution=inst, zone=meta.get("zone", "GLB"),
            kind=meta.get("kind", "portal"), portal=meta.get("portal"),
            count=len(papers), net_score=net, latest=latest,
        ))
    out.sort(key=lambda g: g.count, reverse=True)
    return schemas.ByInstitutionResponse(
        window_days=days, category=category,
        label=catalog.CATEGORIES.get(category, category), groups=out,
    )


@app.get("/api/quant/meta")
def quant_meta():
    """Presets for the quant research center: arXiv categories, asset classes,
    strategies — drive the chips/filters."""
    return {
        "categories": quant.CATEGORIES,
        "assets": list(quant.ASSETS.keys()),
        "strategies": list(quant.STRATEGIES.keys()),
    }


@app.get("/api/quant/search")
def quant_search(
    q: str = Query("", description="free text"),
    cats: Optional[List[str]] = Query(None),
    assets: Optional[List[str]] = Query(None),
    strategies: Optional[List[str]] = Query(None),
    sort: str = Query("submittedDate", description="submittedDate | relevance"),
    limit: int = Query(40, ge=1, le=100),
):
    """Search academic databases (arXiv q-fin) for quant/algo trading papers by
    asset and strategy. Defaults to recent Trading & Microstructure papers."""
    if not (q or assets or strategies):
        cats = cats or ["q-fin.TR"]  # sensible default feed: newest microstructure/trading
    papers = quant.search(q=q, cats=cats, assets=assets, strategies=strategies,
                          max_results=limit, sort=sort)
    return {"count": len(papers), "query": quant.build_query(q, cats, assets, strategies),
            "papers": papers}


@app.get("/api/quant/pdf")
def quant_pdf(url: str = Query(...)):
    """Proxy an arXiv PDF so it can be embedded inline (arXiv blocks framing).
    Restricted to arxiv.org to avoid an open proxy."""
    if not url.startswith(("https://arxiv.org/pdf/", "http://arxiv.org/pdf/")):
        raise HTTPException(status_code=400, detail="Only arxiv.org PDFs allowed")
    try:
        upstream = netguard.safe_get(url, stream=True, headers=PDF_HEADERS, timeout=30)
        upstream.raise_for_status()
        return StreamingResponse(
            upstream.iter_content(chunk_size=8192),
            media_type=upstream.headers.get("Content-Type", "application/pdf"),
            headers={"Content-Disposition": 'inline; filename="paper.pdf"'},
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch PDF: {e}")


@app.get("/api/transactions", response_model=schemas.TransactionsResponse)
def get_transactions(
    days: int = Query(120, ge=1, le=730),
    limit: int = Query(150, ge=1, le=400),
    category: Optional[str] = Query(None, description="commercial_bank | fund"),
    pair: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Published FX calls extracted from bank/fund research: institution, pair,
    directional bias and the note's thesis. No fabricated entry/TP/SL — those
    are a licensed trade-rec product."""
    now = utcnow()
    cutoff = now - timedelta(days=days)
    cats = [category] if category else ["commercial_bank", "fund"]
    src_names = [s for c in cats for s in catalog.sources_in_category(c)]
    rows = (
        db.query(models.Paper)
        .filter(models.Paper.source.in_(src_names))
        .filter(models.Paper.published_date <= now)
        .filter(models.Paper.published_date >= cutoff)
        .order_by(desc(models.Paper.published_date))
        .all()
    )
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
            if len(calls) >= limit:
                break
        if len(calls) >= limit:
            break
    return schemas.TransactionsResponse(window_days=days, count=len(calls), calls=calls)


# Region tag -> currency bloc shown on the cockpit board.
CURRENCY_BLOCS = mcp_mod.CURRENCY_BLOCS


@app.get("/api/board", response_model=schemas.BoardResponse)
def get_board(days: int = Query(7, ge=1, le=365), db: Session = Depends(get_db)):
    """Net FinBERT sentiment per currency bloc over the window. Drives the
    top cockpit strip."""
    now = utcnow()
    cutoff = now - timedelta(days=days)
    rows = (
        db.query(models.Paper)
        .filter(models.Paper.analyzed_at.isnot(None))
        .filter(models.Paper.published_date >= cutoff)
        .filter(models.Paper.published_date <= now)
        .all()
    )
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


# --- Macro event calendar: ForexFactory weekly feed (backend/events.py) ---
@app.get("/api/events", response_model=List[schemas.MacroEvent])
def get_events(limit: int = Query(12, ge=1, le=40), db: Session = Depends(get_db)):
    rows, _err = events_mod.upcoming(limit=limit)
    recent = (db.query(models.Paper).options(defer(models.Paper.full_text))
              .filter(models.Paper.published_date >= utcnow() - timedelta(days=21))
              .filter(models.Paper.published_date <= utcnow()).all())
    out = []
    for e in rows:
        related = sum(1 for p in recent if e["bloc"] in (p.area or []) and e["theme"] in (p.thematic_tags or []))
        out.append(schemas.MacroEvent(date=e["date"], days_until=e["days_until"], name=e["name"],
                                      bloc=e["bloc"], region=e["region"], importance=e["importance"],
                                      theme=e["theme"], related_count=related))
    return out


@app.post("/api/digest", response_model=schemas.DigestResponse)
def build_digest(req: schemas.DigestRequest, db: Session = Depends(get_db)):
    """Net sentiment summary over a set of paper ids (the user's watchlist)."""
    papers = (
        db.query(models.Paper).filter(models.Paper.id.in_(req.ids)).all()
        if req.ids else []
    )
    scored = [p for p in papers if p.sentiment_score is not None]
    net = round(sum(p.sentiment_score for p in scored) / len(scored), 4) if scored else 0.0
    label = "bullish" if net > 0.03 else "bearish" if net < -0.03 else "neutral"

    bloc_acc = {}
    for p in scored:
        for code, region in CURRENCY_BLOCS:
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
        generated_at=utcnow(),
    )


@app.post("/api/watchlist")
def save_watchlist(req: schemas.DigestRequest):
    """Persist the watchlist ids server-side so the digest cron can email them."""
    import json
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "watchlist.json")
    with open(path, "w") as f:
        json.dump({"ids": req.ids[:5000], "updated_at": utcnow().isoformat() + "Z"}, f)
    return {"saved": len(req.ids)}


@app.get("/api/papers/{paper_id}", response_model=schemas.PaperResponse)
def get_paper(paper_id: str, db: Session = Depends(get_db)):
    paper = db.query(models.Paper).filter(models.Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


class ThesisUpdate(BaseModel):
    thesis: Optional[str] = None
    thesis_tags: List[str] = []


@app.patch("/api/papers/{paper_id}/thesis", response_model=schemas.PaperResponse)
def set_paper_thesis(paper_id: str, req: ThesisUpdate, db: Session = Depends(get_db)):
    """Attach (or detach) a paper from a macro research thesis of the hub."""
    paper = db.query(models.Paper).filter(models.Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    paper.thesis = req.thesis
    paper.thesis_tags = req.thesis_tags or []
    db.commit()
    db.refresh(paper)
    return paper


@app.post("/api/papers/{paper_id}/analyze", response_model=schemas.PaperResponse)
def analyze_one(paper_id: str, db: Session = Depends(get_db)):
    paper = db.query(models.Paper).filter(models.Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    analysis.analyze_paper(paper)
    db.commit()
    db.refresh(paper)
    return paper


@app.post("/api/analyze")
def analyze_recent_endpoint(
    background_tasks: BackgroundTasks,
    days: int = Query(7, ge=1, le=90),
):
    """Kick off analysis of recent unanalyzed papers in the background."""
    def _job(days: int):
        db = SessionLocal()
        try:
            analysis.analyze_recent(db, days=days)
        finally:
            db.close()

    background_tasks.add_task(_job, days)
    return {"status": "started", "scope_days": days}


@app.post("/api/ingest")
def trigger_ingest_endpoint(
    background_tasks: BackgroundTasks,
    analyze: bool = Query(True),
):
    """Trigger RSS + scrape ingestion in the background when the user clicks SYNC."""
    if ingest_mod.is_running():
        return {"status": "already_running"}

    def _job():
        ingest_mod.run(do_analyze=analyze, days=7)

    background_tasks.add_task(_job)
    return {"status": "started"}


@app.get("/api/papers/{paper_id}/report")
def paper_report(paper_id: str, lang: str = Query("fr", description="Report language: fr | en | es"), db: Session = Depends(get_db)):
    """Generate a PDF 'compte rendu' for the paper (sentiment + summary)."""
    paper = db.query(models.Paper).filter(models.Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not paper.analyzed_at:
        analysis.analyze_paper(paper)
        db.commit()
        db.refresh(paper)
    pdf_bytes = reports.build_report(paper, lang=lang)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="report-{paper.id[:8]}.pdf"'},
    )


@app.get("/api/papers/{paper_id}/pdfinfo")
def paper_pdfinfo(paper_id: str, db: Session = Depends(get_db)):
    """Whether a full-article PDF is available (resolving + caching the link)."""
    paper = db.query(models.Paper).filter(models.Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    url = pdfscrape.resolve(paper)
    if url and not paper.pdf_url:          # cache discovered link for next time
        paper.pdf_url = url
        db.commit()
    return {"pdf": bool(url), "url": url}


@app.get("/api/papers/{paper_id}/pdf")
def paper_pdf(paper_id: str, db: Session = Depends(get_db)):
    """Proxy the bank's full-article PDF (resolving the link if needed)."""
    paper = db.query(models.Paper).filter(models.Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    url = pdfscrape.resolve(paper)
    if not url:
        raise HTTPException(status_code=404, detail="No PDF available")
    if not paper.pdf_url:
        paper.pdf_url = url
        db.commit()
    try:
        upstream = netguard.safe_get(url, stream=True, headers=PDF_HEADERS, timeout=30)
        upstream.raise_for_status()
        return StreamingResponse(
            upstream.iter_content(chunk_size=8192),
            media_type=upstream.headers.get("Content-Type", "application/pdf"),
            headers={"Content-Disposition": f'inline; filename="{paper.id[:8]}.pdf"'},
        )
    except netguard.UnsafeURL as e:
        raise HTTPException(status_code=400, detail=f"URL refused: {e}")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch PDF: {e}")


@app.get("/api/papers/{paper_id}/download")
def download_paper(paper_id: str, db: Session = Depends(get_db)):
    paper = db.query(models.Paper).filter(models.Paper.id == paper_id).first()
    if not paper or not paper.pdf_url:
        raise HTTPException(status_code=404, detail="Paper PDF not found")
    try:
        upstream = netguard.safe_get(paper.pdf_url, stream=True, headers=PDF_HEADERS, timeout=30)
        upstream.raise_for_status()
        ctype = upstream.headers.get("Content-Type", "application/pdf")
        return StreamingResponse(
            upstream.iter_content(chunk_size=8192),
            media_type=ctype,
            headers={"Content-Disposition": f'inline; filename="{paper.id[:8]}.pdf"'},
        )
    except netguard.UnsafeURL as e:
        raise HTTPException(status_code=400, detail=f"URL refused: {e}")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch PDF: {e}")
