"""Fast full-database migration from legacy FinBERT scores to EmbeddingProp V3.

Phase 1: Batch-embeds all rows in `research.db` (identity vectors via Ollama
`qwen3-embedding:0.6b`, 256d Matryoshka), scores 100% of news with `VectorScorer`
and 100% of papers with `PaperVectorClassifier`, preserves existing rich summaries
on `vector_only` rows, commits in short 250-row WAL transactions, and builds
two persisted `int8` HybridIndexes (`news` + `papers`).

Phase 2: Runs `lfm3:latest` enrichment on the top `--lfm3-top` newest high-signal
documents (>= 62% score) from the last 48 hours.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import timedelta

import numpy as np
from sqlalchemy import desc

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend import analysis, embedding_engine, models  # noqa: E402
from backend.database import SessionLocal, engine, run_migrations  # noqa: E402
from backend.timeutil import utcnow  # noqa: E402
from embedding_prop import (  # noqa: E402
    DocumentAnalysis,
    HybridIndex,
    PaperClassification,
    TextChunk,
    build_identity_text,
    classify_doc_kind,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=64, help="Ollama embed batch size")
    ap.add_argument("--lfm3-top", type=int, default=15, help="Newest >=62% docs to enrich with LFM-3")
    ap.add_argument("--days", type=int, default=None, help="Optional lookback window in days")
    ap.add_argument("--force", action="store_true", help="Re-score rows already marked embedding_prop_v3")
    args = ap.parse_args()

    models.Base.metadata.create_all(bind=engine)
    run_migrations()

    svc = embedding_engine.get_service()
    t0 = time.perf_counter()

    db = SessionLocal()
    try:
        q = db.query(models.Paper).order_by(desc(models.Paper.published_date))
        if args.days:
            cutoff = utcnow() - timedelta(days=int(args.days))
            q = q.filter(models.Paper.published_date >= cutoff)
        all_rows = q.all()
        total = len(all_rows)
        print(f"[*] Phase 1: processing {total} rows from research.db (batch_size={args.batch_size})...")

        news_chunks: list[TextChunk] = []
        news_vecs: list[np.ndarray] = []
        paper_chunks: list[TextChunk] = []
        paper_vecs: list[np.ndarray] = []

        updated = 0
        noise_news = 0
        noise_papers = 0
        commit_every = 250

        for start in range(0, total, args.batch_size):
            batch_rows = all_rows[start : start + args.batch_size]
            docs = [embedding_engine.row_to_doc_input(r) for r in batch_rows]
            texts = [build_identity_text(d) for d in docs]
            vecs = svc.embedder.embed(texts, batch_size=args.batch_size)

            for row, doc, text, vec in zip(batch_rows, docs, texts, vecs):
                kind = classify_doc_kind(doc)
                chunk = TextChunk(
                    chunk_id=f"{doc.id}::identity",
                    doc_id=doc.id,
                    chunk_type="identity",
                    chunk_index=0,
                    text=text,
                )
                if kind == "paper":
                    paper_chunks.append(chunk)
                    paper_vecs.append(vec)
                else:
                    news_chunks.append(chunk)
                    news_vecs.append(vec)

                # Never overwrite LFM-3 enriched records; skip already migrated unless --force
                if row.analysis_tier == "lfm3_enriched":
                    continue
                if not args.force and row.analysis_engine == "embedding_prop_v3":
                    continue

                if kind == "paper":
                    info = svc.paper_engine.classifier.classify(vec, text, doc.content_depth)
                    if not info["is_quant_relevant"]:
                        noise_papers += 1
                    p_res = PaperClassification(
                        doc_id=doc.id,
                        title=doc.title,
                        source=doc.source,
                        published_date=doc.published_date,
                        content_depth=doc.content_depth,
                        is_quant_relevant=info["is_quant_relevant"],
                        relevance_score=info["relevance_score"],
                        trading_applicability_score=info["trading_applicability_score"],
                        strategy_families=info["strategy_families"],
                        quant_concepts=info["quant_concepts"],
                        methodology_type=info["methodology_type"],
                        research_question=(
                            "Filtered out as non-financial / off-topic academic noise."
                            if not info["is_quant_relevant"]
                            else (row.abstract or doc.title)[:240]
                        ),
                        skipped_llm=True,
                        analysis_tier="vector_only" if info["is_quant_relevant"] else "noise_filtered",
                    )
                    svc._apply_paper_result(
                        row, p_res, analysis.derive_currency_pairs, analysis.derive_area
                    )
                else:
                    vs = svc.news_engine.scorer.score(vec, text, doc.source, doc.content_depth)
                    if not vs.is_financial_relevant:
                        noise_news += 1
                    n_res = DocumentAnalysis(
                        doc_id=doc.id,
                        title=doc.title,
                        source=doc.source,
                        published_date=doc.published_date,
                        content_depth=doc.content_depth,
                        is_financial_relevant=vs.is_financial_relevant,
                        semantic_relevance_score=vs.semantic_relevance_score,
                        importance_score=vs.vector_importance_score,
                        themes=vs.themes,
                        summary=(
                            f"Filtered out by Stage 1 semantic noise gate (relevance={vs.semantic_relevance_score:.1f}/100)."
                            if not vs.is_financial_relevant
                            else (row.abstract or doc.title)[:280]
                        ),
                        assets=[],
                        monetary_zones=vs.monetary_zones if vs.is_financial_relevant else ["GLB"],
                        overall_sentiment_label=vs.vector_sentiment_label if vs.is_financial_relevant else "neutral",
                        overall_sentiment_score=vs.vector_sentiment_score if vs.is_financial_relevant else 0.0,
                        skipped_llm=True,
                        analysis_tier="vector_only" if vs.is_financial_relevant else "noise_filtered",
                    )
                    svc._apply_news_result(
                        row, n_res, analysis.derive_currency_pairs, analysis.derive_area
                    )
                updated += 1

            if (start + len(batch_rows)) % commit_every < args.batch_size or (start + len(batch_rows)) == total:
                db.commit()

            done_so_far = start + len(batch_rows)
            if done_so_far % 1024 < args.batch_size or done_so_far == total:
                elapsed = time.perf_counter() - t0
                rate = done_so_far / max(0.1, elapsed)
                print(
                    f"    [{done_so_far}/{total}] updated={updated} rate={rate:.1f} docs/s "
                    f"elapsed={elapsed:.1f}s (noise: news={noise_news}, papers={noise_papers})"
                )

        # Build & persist full int8 HybridIndexes under cross-process lock
        t_idx = time.perf_counter()
        n_idx = HybridIndex()
        if news_chunks:
            n_idx.add_chunks(news_chunks, np.vstack(news_vecs))
        p_idx = HybridIndex()
        if paper_chunks:
            p_idx.add_chunks(paper_chunks, np.vstack(paper_vecs))

        with svc._process_index_lock():
            n_save = n_idx.save(svc.news_dir)
            svc._mtimes["news"] = svc._meta_mtime(svc.news_dir)
            p_save = p_idx.save(svc.papers_dir)
            svc._mtimes["papers"] = svc._meta_mtime(svc.papers_dir)
        print(
            f"[=] Indexes built in {time.perf_counter() - t_idx:.2f}s | "
            f"news={n_save['doc_count']} docs ({n_save['vectors_bytes']/1024:.1f} KB int8) | "
            f"papers={p_save['doc_count']} docs ({p_save['vectors_bytes']/1024:.1f} KB int8)"
        )

        # Phase 2: Enrich top newest >= 62% docs via LFM-3 (10 news + 5 papers by default)
        if args.lfm3_top > 0:
            now = utcnow()
            cut_48h = now - timedelta(hours=48)
            n_limit = max(1, (args.lfm3_top * 2) // 3)
            p_limit = max(1, args.lfm3_top - n_limit)

            top_news = (
                db.query(models.Paper)
                .filter(models.Paper.doc_kind == "news")
                .filter(models.Paper.analysis_tier == "vector_only")
                .filter(models.Paper.impact_score >= 62.0)
                .filter(models.Paper.published_date <= now, models.Paper.published_date >= cut_48h)
                .order_by(desc(models.Paper.impact_score), desc(models.Paper.published_date))
                .limit(n_limit)
                .all()
            )
            top_papers = (
                db.query(models.Paper)
                .filter(models.Paper.doc_kind == "paper")
                .filter(models.Paper.analysis_tier == "vector_only")
                .filter(models.Paper.impact_score >= 62.0)
                .filter(models.Paper.published_date <= now)
                .order_by(desc(models.Paper.impact_score), desc(models.Paper.published_date))
                .limit(p_limit)
                .all()
            )
            targets = top_news + top_papers
            print(f"[*] Phase 2: LFM-3 enriching {len(top_news)} news + {len(top_papers)} papers (>= 62%)...")
            if targets:
                svc.analyze_rows(
                    targets,
                    fallback_pairs=analysis.derive_currency_pairs,
                    fallback_area=analysis.derive_area,
                    max_llm_calls=len(targets),
                )
                db.commit()
                for t in targets:
                    print(f"    [lfm3:{t.doc_kind}] imp={t.impact_score:.0f} {t.source}: {t.title[:65]}")

        print(f"[=] BACKFILL COMPLETE in {time.perf_counter() - t0:.1f}s")
    finally:
        db.close()


if __name__ == "__main__":
    main()
