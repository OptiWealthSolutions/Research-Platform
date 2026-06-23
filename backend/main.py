from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from typing import List, Optional
from datetime import datetime, timedelta
import os
import requests
from fastapi.responses import StreamingResponse, Response

from . import models, schemas, analysis, reports, fxcalls, pdfscrape
from . import sources as catalog
from .database import SessionLocal, engine, get_db, run_migrations

# Create / migrate DB tables
models.Base.metadata.create_all(bind=engine)
run_migrations()

app = FastAPI(title="Macroeconomic Research Aggregator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(models.Paper)

    # Drop bad/future-dated rows: feeds sometimes emit garbage dates (e.g. 2035)
    # because the real date lives in a field the parser missed.
    query = query.filter(models.Paper.published_date <= datetime.now())

    if horizon_days:
        cutoff = datetime.now() - timedelta(days=int(horizon_days))
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
    if zone:
        names = [s["name"] for s in catalog.CATALOG if s["zone"] == zone]
        query = query.filter(models.Paper.source.in_(names)) if names else query.filter(False)
    if sentiment:
        query = query.filter(models.Paper.sentiment_label == sentiment.lower())
    if search:
        like = f"%{search}%"
        query = query.filter(
            models.Paper.title.ilike(like) | models.Paper.abstract.ilike(like)
        )

    papers = query.order_by(desc(models.Paper.published_date)).all()

    # JSON-array tag filtering (SQLite-friendly: done in Python)
    out = []
    for p in papers:
        if thematic_tags and not any(t in (p.thematic_tags or []) for t in thematic_tags):
            continue
        if country_tags and not any(t in (p.country_tags or []) for t in country_tags):
            continue
        if pair and pair not in (p.currency_pairs or []):
            continue
        out.append(p)

    return out[skip:skip + limit]


@app.get("/api/stats", response_model=schemas.StatsResponse)
def get_stats(db: Session = Depends(get_db)):
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


@app.get("/api/central-banks", response_model=schemas.CentralBankResponse)
def get_central_banks(
    days: int = Query(30, ge=1, le=365),
    per_zone: int = Query(6, ge=1, le=30),
    db: Session = Depends(get_db),
):
    """Recent central-bank output grouped by monetary zone (press releases,
    speeches, publications). Powers the Central Banks view."""
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
    now = datetime.now()
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
CURRENCY_BLOCS = [
    ("USD", "US"),
    ("EUR", "EU"),
    ("GBP", "UK"),
    ("JPY", "Japan"),
    ("CNY", "China"),
    ("GLB", "Global"),
]


@app.get("/api/board", response_model=schemas.BoardResponse)
def get_board(days: int = Query(7, ge=1, le=365), db: Session = Depends(get_db)):
    """Net FinBERT sentiment per currency bloc over the window. Drives the
    top cockpit strip."""
    now = datetime.now()
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


# --- Macro event calendar (scheduled central-bank meetings + key releases) ---
# Dates are the published 2026 schedules; treat as scheduled estimates.
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


@app.get("/api/events", response_model=List[schemas.MacroEvent])
def get_events(limit: int = Query(12, ge=1, le=40), db: Session = Depends(get_db)):
    today = datetime.now().date()
    recent_cut = datetime.now() - timedelta(days=21)
    recent = (
        db.query(models.Paper)
        .filter(models.Paper.published_date >= recent_cut)
        .filter(models.Paper.published_date <= datetime.now())
        .all()
    )
    out = []
    for date_s, name, bloc, region, importance, theme in MACRO_CALENDAR:
        d = datetime.strptime(date_s, "%Y-%m-%d").date()
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
        generated_at=datetime.now(),
    )


@app.post("/api/watchlist")
def save_watchlist(req: schemas.DigestRequest):
    """Persist the watchlist ids server-side so the digest cron can email them."""
    import json
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "watchlist.json")
    with open(path, "w") as f:
        json.dump({"ids": req.ids, "updated_at": datetime.now().isoformat()}, f)
    return {"saved": len(req.ids)}


@app.get("/api/papers/{paper_id}", response_model=schemas.PaperResponse)
def get_paper(paper_id: str, db: Session = Depends(get_db)):
    paper = db.query(models.Paper).filter(models.Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
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


@app.get("/api/papers/{paper_id}/report")
def paper_report(paper_id: str, db: Session = Depends(get_db)):
    """Generate a PDF 'compte rendu' for the paper (sentiment + summary)."""
    paper = db.query(models.Paper).filter(models.Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not paper.analyzed_at:
        analysis.analyze_paper(paper)
        db.commit()
        db.refresh(paper)
    pdf_bytes = reports.build_report(paper)
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
        upstream = requests.get(url, stream=True, headers=PDF_HEADERS, timeout=30)
        upstream.raise_for_status()
        return StreamingResponse(
            upstream.iter_content(chunk_size=8192),
            media_type=upstream.headers.get("Content-Type", "application/pdf"),
            headers={"Content-Disposition": f'inline; filename="{paper.id[:8]}.pdf"'},
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch PDF: {e}")


@app.get("/api/papers/{paper_id}/download")
def download_paper(paper_id: str, db: Session = Depends(get_db)):
    paper = db.query(models.Paper).filter(models.Paper.id == paper_id).first()
    if not paper or not paper.pdf_url:
        raise HTTPException(status_code=404, detail="Paper PDF not found")
    try:
        upstream = requests.get(paper.pdf_url, stream=True, headers=PDF_HEADERS, timeout=30)
        upstream.raise_for_status()
        ctype = upstream.headers.get("Content-Type", "application/pdf")
        return StreamingResponse(
            upstream.iter_content(chunk_size=8192),
            media_type=ctype,
            headers={"Content-Disposition": f'inline; filename="{paper.id[:8]}.pdf"'},
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch PDF: {e}")
