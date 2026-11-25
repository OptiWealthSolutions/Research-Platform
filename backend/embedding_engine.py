"""EmbeddingProp service bridge for Research Platform.

Replaces FinBERT with the dual-engine architecture from `embedding_prop`:
- Single shared `qwen3-embedding:0.6b` (256d Matryoshka, int8 incremental HybridIndex)
- Track A (`MarketNewsEngine`): importance 0-100, zones, assets, directional sentiment,
  with LFM-3 enrichment on all relevant news scoring >= 62%.
- Track B (`QuantPaperEngine`): strategy families, quant concepts, applicability 0-100,
  with LFM-3 research takeaway on all relevant papers scoring >= 62%.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import sys
import threading
from pathlib import Path
from typing import Iterable

from . import nlp
from .timeutil import utcnow

_ROOT = Path(__file__).resolve().parents[1]
_EP_SRC = _ROOT.parent / "embedding_prop" / "src"
if _EP_SRC.exists() and str(_EP_SRC) not in sys.path:
    # Allow running directly even before editable install if needed.
    try:
        import embedding_prop  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(_EP_SRC))

from embedding_prop import (  # noqa: E402
    DocumentAnalysis,
    DocumentInput,
    HybridIndex,
    MarketNewsEngine,
    OllamaEmbedder,
    PaperClassification,
    QuantPaperEngine,
    Settings,
    classify_doc_kind,
)


def _dir_to_db_label(direction: str | None) -> str:
    low = (direction or "neutral").lower()
    if low in ("bullish", "positive"):
        return "positive"
    if low in ("bearish", "negative"):
        return "negative"
    return "neutral"


def row_to_doc_input(paper) -> DocumentInput:
    """Convert a Research Platform SQLAlchemy Paper row into `DocumentInput`."""
    raw_ts = getattr(paper, "text_source", None)
    if raw_ts in ("pdf", "html", "abstract"):
        text_source = raw_ts
    elif getattr(paper, "full_text", None):
        text_source = "html"
    else:
        text_source = "snippet"

    pub = getattr(paper, "published_date", None)
    pub_str = pub.isoformat() + "Z" if pub is not None and hasattr(pub, "isoformat") else (str(pub) if pub else None)

    return DocumentInput(
        id=str(paper.id),
        title=str(paper.title or ""),
        source=str(paper.source or "Unknown"),
        text_source=text_source,
        abstract=str(paper.abstract or ""),
        full_text=str(paper.full_text or ""),
        published_date=pub_str,
        url=getattr(paper, "source_url", None) or getattr(paper, "pdf_url", None),
        existing_impact_score=getattr(paper, "impact_score", None),
        existing_sentiment=getattr(paper, "sentiment_label", None),
    )


class EmbeddingPropService:
    """Thread-safe singleton wrapping shared OllamaEmbedder, dual engines, and int8 indexes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.settings = Settings(
            embed_dim=256,
            llm_importance_threshold=62.0,
            paper_applicability_threshold=62.0,
            top_k_llm=None,
            top_k_papers_llm=None,
        )
        self.embedder = OllamaEmbedder(self.settings)
        self.news_engine = MarketNewsEngine(settings=self.settings, embedder=self.embedder)
        self.paper_engine = QuantPaperEngine(settings=self.settings, embedder=self.embedder)

        self.index_root = Path(
            os.environ.get("RP_INDEX_DIR", str(_ROOT / "data" / "embedding_prop_index"))
        )
        self.index_root.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.index_root / ".index.lock"
        self.news_dir = self.index_root / "news"
        self.papers_dir = self.index_root / "papers"
        self._mtimes: dict[str, int] = {"news": 0, "papers": 0}
        self.news_index = self._load_or_new(self.news_dir, "news")
        self.papers_index = self._load_or_new(self.papers_dir, "papers")

    @contextlib.contextmanager
    def _process_index_lock(self):
        self.index_root.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _meta_mtime(self, path: Path) -> int:
        meta = path / "meta.json"
        return meta.stat().st_mtime_ns if meta.exists() else 0

    def _load_or_new(self, path: Path, track: str | None = None) -> HybridIndex:
        meta = path / "meta.json"
        if meta.exists():
            try:
                idx = HybridIndex.load(path)
                if track:
                    self._mtimes[track] = meta.stat().st_mtime_ns
                return idx
            except Exception:
                pass
        return HybridIndex()

    def _sync_index_from_disk(self, track: str) -> HybridIndex:
        path = self.papers_dir if track == "papers" else self.news_dir
        cur_mtime = self._meta_mtime(path)
        if cur_mtime > self._mtimes.get(track, 0):
            loaded = self._load_or_new(path, track)
            if track == "papers":
                self.papers_index = loaded
            else:
                self.news_index = loaded
        return self.papers_index if track == "papers" else self.news_index

    def _apply_news_result(self, paper, res: DocumentAnalysis, fallback_pairs, fallback_area) -> dict:
        paper.doc_kind = "news"
        paper.analysis_tier = res.analysis_tier
        paper.analysis_engine = "embedding_prop_v3"
        paper.relevance_score = res.semantic_relevance_score
        paper.content_depth = res.content_depth
        paper.impact_score = res.importance_score
        paper.sentiment_score = res.overall_sentiment_score
        paper.sentiment_label = _dir_to_db_label(res.overall_sentiment_label)
        s = float(res.overall_sentiment_score)
        pos_w = max(0.0, s)
        neg_w = max(0.0, -s)
        neu_w = max(0.0, 1.0 - abs(s))
        tot_w = pos_w + neg_w + neu_w or 1.0
        paper.sentiment_detail = {
            "positive": round(pos_w / tot_w, 4),
            "negative": round(neg_w / tot_w, 4),
            "neutral": round(neu_w / tot_w, 4),
        }
        existing_summary = (paper.summary or "").strip()
        if res.analysis_tier == "vector_only" and existing_summary and not existing_summary.startswith(("LFM3 fallback", "[Vector-")):
            paper.summary = existing_summary
        else:
            paper.summary = res.summary
        paper.area = res.monetary_zones or fallback_area(paper)
        paper.asset_impacts = [a.model_dump() for a in res.assets]
        paper.quant_taxonomy = {}

        # Derive FX pairs cleanly from extracted assets when available, else metadata helper.
        fx_pairs = [
            a.asset
            for a in res.assets
            if a.asset_class == "FX" and ("/" in a.asset or a.asset == "DXY")
        ]
        paper.currency_pairs = fx_pairs[:5] if fx_pairs else fallback_pairs(paper)

        if not (paper.keywords or []):
            blob = f"{paper.title or ''} {res.summary or ''}"
            paper.keywords = nlp.extract_keywords(blob, top_n=8)

        paper.interest_score = max(1, min(5, round(res.importance_score / 20.0)))
        paper.analyzed_at = utcnow()
        return {
            "doc_kind": "news",
            "analysis_tier": res.analysis_tier,
            "sentiment_label": paper.sentiment_label,
            "sentiment_score": paper.sentiment_score,
            "impact_score": paper.impact_score,
            "area": paper.area,
            "asset_impacts": paper.asset_impacts,
            "summary": paper.summary,
        }

    def _apply_paper_result(self, paper, res: PaperClassification, fallback_pairs, fallback_area) -> dict:
        paper.doc_kind = "paper"
        paper.analysis_tier = res.analysis_tier
        paper.analysis_engine = "embedding_prop_v3"
        paper.relevance_score = res.relevance_score
        paper.content_depth = res.content_depth
        paper.impact_score = res.trading_applicability_score
        # Papers are NOT directional market sentiment signals.
        paper.sentiment_label = "neutral"
        paper.sentiment_score = 0.0
        paper.sentiment_detail = {"positive": 0.0, "negative": 0.0, "neutral": 1.0}
        existing_summary = (paper.summary or "").strip()
        summary_parts = [p for p in (res.research_question, res.methodology_summary, res.trading_takeaway) if p]
        if res.analysis_tier == "lfm3_enriched" and summary_parts:
            paper.summary = " ".join(summary_parts)
        elif existing_summary and not existing_summary.startswith(("LFM3 fallback", "[Vector-")):
            paper.summary = existing_summary
        else:
            paper.summary = " ".join(summary_parts) if summary_parts else (paper.abstract or paper.title)
        paper.quant_taxonomy = {
            "trading_applicability_score": res.trading_applicability_score,
            "strategy_families": [f.model_dump() for f in res.strategy_families],
            "quant_concepts": res.quant_concepts,
            "methodology_type": res.methodology_type,
            "research_question": res.research_question,
            "methodology_summary": res.methodology_summary,
            "trading_takeaway": res.trading_takeaway,
        }
        paper.asset_impacts = []
        paper.area = fallback_area(paper)
        paper.currency_pairs = fallback_pairs(paper)
        if not (paper.keywords or []):
            paper.keywords = res.quant_concepts[:8] or nlp.extract_keywords(
                f"{paper.title or ''} {paper.summary or ''}", top_n=8
            )
        paper.interest_score = max(1, min(5, round(res.trading_applicability_score / 20.0)))
        paper.analyzed_at = utcnow()
        return {
            "doc_kind": "paper",
            "analysis_tier": res.analysis_tier,
            "sentiment_label": paper.sentiment_label,
            "sentiment_score": paper.sentiment_score,
            "impact_score": paper.impact_score,
            "quant_taxonomy": paper.quant_taxonomy,
            "summary": paper.summary,
        }

    def analyze_rows(
        self,
        papers: Iterable,
        *,
        fallback_pairs,
        fallback_area,
        max_llm_calls: int | None = None,
        use_llm: bool | None = None,
    ) -> list[dict]:
        """Analyze one or more ORM Paper rows and update persistent int8 indexes."""
        paper_list = list(papers)
        if not paper_list:
            return []

        docs_by_id: dict[str, tuple[object, DocumentInput, str]] = {}
        news_docs: list[DocumentInput] = []
        research_docs: list[DocumentInput] = []

        for row in paper_list:
            doc = row_to_doc_input(row)
            kind = classify_doc_kind(doc)
            docs_by_id[doc.id] = (row, doc, kind)
            if kind == "paper":
                research_docs.append(doc)
            else:
                news_docs.append(doc)

        with self._lock, self._process_index_lock():
            if news_docs:
                n_idx = self._sync_index_from_disk("news")
                st_n = n_idx.add_documents_incremental(news_docs, self.embedder)
                if st_n["added_docs"] > 0:
                    n_idx.save(self.news_dir)
                    self._mtimes["news"] = self._meta_mtime(self.news_dir)
            if research_docs:
                p_idx = self._sync_index_from_disk("papers")
                st_p = p_idx.add_documents_incremental(research_docs, self.embedder)
                if st_p["added_docs"] > 0:
                    p_idx.save(self.papers_dir)
                    self._mtimes["papers"] = self._meta_mtime(self.papers_dir)

        effective_use_llm = self.settings.use_llm_by_default if use_llm is None else use_llm
        news_results = (
            {
                r.doc_id: r
                for r in self.news_engine.analyze_batch(
                    news_docs,
                    selective_llm=True,
                    max_llm_calls=max_llm_calls,
                    use_llm=effective_use_llm,
                )
            }
            if news_docs
            else {}
        )
        paper_results = (
            {
                r.doc_id: r
                for r in self.paper_engine.analyze_batch(
                    research_docs,
                    selective_llm=True,
                    max_llm_calls=max_llm_calls,
                    use_llm=effective_use_llm,
                )
            }
            if research_docs
            else {}
        )

        outputs: list[dict] = []
        for doc_id, (row, _, kind) in docs_by_id.items():
            if kind == "paper" and doc_id in paper_results:
                outputs.append(
                    self._apply_paper_result(row, paper_results[doc_id], fallback_pairs, fallback_area)
                )
            elif doc_id in news_results:
                outputs.append(
                    self._apply_news_result(row, news_results[doc_id], fallback_pairs, fallback_area)
                )
        return outputs

    def seed_from_db_if_empty(self, db, *, track: str = "news", limit: int = 200) -> int:
        """Seed a sparse/empty int8 index with recent DB rows (identity vectors only -> ~3s)."""
        with self._lock, self._process_index_lock():
            idx = self._sync_index_from_disk(track)
            if len(idx.indexed_doc_ids) >= 20:
                return 0
        from . import models
        from .timeutil import utcnow
        from sqlalchemy import desc
        from sqlalchemy.orm import defer

        rows = (
            db.query(models.Paper)
            .options(defer(models.Paper.full_text))
            .filter(models.Paper.published_date <= utcnow())
            .order_by(desc(models.Paper.published_date))
            .limit(limit * 3)
            .all()
        )
        want_kind = "paper" if track == "papers" else "news"
        picked = [
            row_to_doc_input(r)
            for r in rows
            if classify_doc_kind(row_to_doc_input(r)) == want_kind
        ][:limit]
        if not picked:
            return 0
        with self._lock, self._process_index_lock():
            idx = self._sync_index_from_disk(track)
            if len(idx.indexed_doc_ids) < 20:
                st = idx.add_documents_incremental(picked, self.embedder)
                if st["added_docs"] > 0:
                    target_dir = self.papers_dir if track == "papers" else self.news_dir
                    idx.save(target_dir)
                    self._mtimes[track] = self._meta_mtime(target_dir)
                return int(st["added_docs"])
        return 0

    def semantic_search(
        self,
        query: str,
        *,
        track: str = "news",
        top_k: int = 10,
        alpha: float = 0.7,
        db=None,
    ) -> list[dict]:
        """Run hybrid 256d int8 cosine + BM25 search over the requested track."""
        with self._lock:
            idx = self._sync_index_from_disk(track)
        if len(idx.indexed_doc_ids) < 20 and db is not None:
            self.seed_from_db_if_empty(db, track=track, limit=200)
            with self._lock:
                idx = self._sync_index_from_disk(track)
        if not idx.chunks:
            return []
        q_vec = self.embedder.embed_one(query)
        with self._lock:
            hits = idx.search(query, q_vec, top_k=top_k * 2, alpha=alpha)

        # Deduplicate by doc_id keeping the highest-scoring chunk hit
        seen: set[str] = set()
        out: list[dict] = []
        for h in hits:
            if h.doc_id in seen:
                continue
            seen.add(h.doc_id)
            out.append(
                {
                    "doc_id": h.doc_id,
                    "score": round(h.score, 4),
                    "dense_score": round(h.dense_score, 4),
                    "bm25_score": round(h.bm25_score, 4),
                    "matched_chunk_type": h.chunk_type,
                    "matched_excerpt": h.text[:360],
                }
            )
            if len(out) >= top_k:
                break
        return out


_SERVICE: EmbeddingPropService | None = None
_SERVICE_LOCK = threading.Lock()


def get_service() -> EmbeddingPropService:
    """Lazy-initialize the shared EmbeddingPropService singleton."""
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = EmbeddingPropService()
    return _SERVICE
