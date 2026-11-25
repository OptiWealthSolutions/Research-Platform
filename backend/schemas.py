from pydantic import BaseModel, field_validator
from typing import Any, List, Optional, Dict
from datetime import datetime


class PaperBase(BaseModel):
    title: str
    authors: Optional[str] = None
    abstract: Optional[str] = None
    published_date: Optional[datetime] = None
    published_precision: Optional[str] = None
    source: Optional[str] = None
    country_tags: List[str] = []
    thematic_tags: List[str] = []
    currency_pairs: List[str] = []
    created_at: Optional[datetime] = None
    pdf_url: Optional[str] = None
    source_url: Optional[str] = None
    image_url: Optional[str] = None
    doi: Optional[str] = None

    # Analysis layer
    summary: Optional[str] = None
    keywords: List[str] = []
    sentiment_label: Optional[str] = None
    sentiment_score: Optional[float] = None
    sentiment_detail: Dict[str, Any] = {}
    interest_score: Optional[int] = None
    impact_score: Optional[float] = None
    analyzed_at: Optional[datetime] = None

    # Research thesis linkage
    thesis: Optional[str] = None
    thesis_tags: List[str] = []

    # Multi-asset / Area
    area: List[str] = []

    # EmbeddingProp Dual-Engine fields
    doc_kind: Optional[str] = None
    analysis_tier: Optional[str] = None
    analysis_engine: Optional[str] = None
    relevance_score: Optional[float] = None
    content_depth: Optional[str] = None
    asset_impacts: List[dict] = []
    quant_taxonomy: dict = {}

    @field_validator(
        "country_tags", "thematic_tags", "keywords", "currency_pairs",
        "thesis_tags", "area", "asset_impacts", mode="before"
    )
    @classmethod
    def _none_to_list(cls, v):
        return v or []

    @field_validator("sentiment_detail", "quant_taxonomy", mode="before")
    @classmethod
    def _none_to_dict(cls, v):
        return v or {}


class PaperCreate(PaperBase):
    clean_title_hash: str


class PaperResponse(PaperBase):
    id: str

    class Config:
        from_attributes = True


class StatsResponse(BaseModel):
    total: int
    analyzed: int
    sentiment: Dict[str, int]
    sources: Dict[str, int]
    last_week: int


class BlocSentiment(BaseModel):
    bloc: str          # currency code e.g. USD
    region: str        # internal region tag e.g. US
    score: float       # mean signed sentiment in [-1, 1]
    count: int         # analyzed papers in window
    positive: int
    negative: int
    neutral: int


class BoardResponse(BaseModel):
    window_days: int
    blocs: List[BlocSentiment]


class MacroEvent(BaseModel):
    date: str            # ISO date
    days_until: int
    name: str
    bloc: str            # currency bloc most affected
    region: str
    importance: str      # high | medium
    theme: str
    related_count: int = 0


class SourceItem(BaseModel):
    name: str
    institution: str
    domain: str
    kind: str            # rss | scrape | portal
    category: str        # central_bank | sell_side | multilateral | academic
    zone: str            # monetary zone code
    portal: Optional[str] = None
    feed: Optional[str] = None
    count: int = 0       # docs we currently hold from this source
    last_published: Optional[str] = None       # ISO date du dernier article
    days_since_last: Optional[int] = None      # jours depuis le dernier article
    last_error: Optional[str] = None           # dernière erreur d'ingestion (timeout, 403, etc.)
    status: Optional[str] = None               # "healthy" | "warning" | "error"


class SourceCatalogResponse(BaseModel):
    categories: Dict[str, str]
    zones: Dict[str, dict]
    sources: List[SourceItem]


class CBItem(BaseModel):
    id: str
    title: str
    source: str
    published_date: Optional[datetime] = None
    sentiment_label: Optional[str] = None
    sentiment_score: Optional[float] = None
    source_url: Optional[str] = None
    pdf_url: Optional[str] = None


class CBZone(BaseModel):
    zone: str
    label: str
    ccy: str
    flag: str
    authority: str
    count: int
    net_score: Optional[float] = None
    institutions: List[str] = []
    latest: List[CBItem] = []


class CentralBankResponse(BaseModel):
    window_days: int
    zones: List[CBZone]


class InstitutionGroup(BaseModel):
    institution: str
    zone: str
    kind: str            # rss | article_scrape | portal
    portal: Optional[str] = None
    count: int
    net_score: Optional[float] = None
    latest: List[CBItem] = []


class ByInstitutionResponse(BaseModel):
    window_days: int
    category: str
    label: str
    groups: List[InstitutionGroup]


class FxCall(BaseModel):
    paper_id: str
    institution: str
    source: str
    category: str
    zone: str
    date: Optional[datetime] = None
    pair: str
    bias: str            # bullish | bearish | neutral
    score: Optional[float] = None
    thesis: str          # the note's title
    url: Optional[str] = None


class TransactionsResponse(BaseModel):
    window_days: int
    count: int
    calls: List[FxCall]


class DigestRequest(BaseModel):
    ids: List[str] = []


class DigestBloc(BaseModel):
    bloc: str
    score: float
    count: int


class DigestResponse(BaseModel):
    count: int
    analyzed: int
    net_score: float
    net_label: str
    positive: int
    negative: int
    neutral: int
    by_bloc: List[DigestBloc]
    top_bullish: List[dict]
    top_bearish: List[dict]
    generated_at: datetime


# ---------------------------------------------------------------------------
# Comparaison de sources (ton / biais par institution sur une période + zone)
# ---------------------------------------------------------------------------
class SourceCompare(BaseModel):
    source: str
    institution: Optional[str] = None
    zone: str
    count: int                     # papiers dans la fenêtre
    analyzed: int                  # papiers analysés (EmbeddingProp / score signé)
    avg_sentiment: float           # score moyen signé [-1, 1]
    positive: int
    negative: int
    neutral: int
    net_score: float               # (pos - neg) / analyzed


class CompareResponse(BaseModel):
    window_days: int
    zones: List[str]
    sources: List[SourceCompare]


# ---------------------------------------------------------------------------
# Santé des sources RSS
# ---------------------------------------------------------------------------
class SourceHealth(BaseModel):
    name: str
    kind: str
    zone: str
    last_published: Optional[str] = None       # ISO date du dernier article
    days_since_last: Optional[int] = None      # jours depuis le dernier article
    status: str                                # "healthy" | "warning" | "error"
    total_docs: int                            # nombre total d'articles en BDD
    last_error: Optional[str] = None           # dernière erreur d'ingestion (timeout, 403, etc.)


class HealthResponse(BaseModel):
    overall: str                               # "healthy" | "degraded" | "error"
    sources: List[SourceHealth]
