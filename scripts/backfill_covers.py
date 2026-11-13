"""Backfill des image_url (covers) pour les articles déjà en base qui n'en ont pas.
Traite les articles les plus récents en premier (fenêtre configurable, défaut 30j).

Run:  /opt/anaconda3/bin/python3 scripts/backfill_covers.py [--days N] [--limit N]
"""
import sys
import os
import time
import argparse
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.database import SessionLocal, engine, run_migrations
from backend.models import Paper
from backend import sources as catalog
from scripts.ingest import fetch_cover_image


def run(days: int = 30, limit: int = 200, delay: float = 0.3):
    Base = __import__("backend.models", fromlist=["Base"]).Base
    Base.metadata.create_all(bind=engine)
    run_migrations()

    db = SessionLocal()
    try:
        cutoff = datetime.now() - timedelta(days=days)
        rows = (
            db.query(Paper)
            .filter(Paper.published_date >= cutoff)
            .filter(
                (Paper.image_url.is_(None)) | (Paper.image_url == "")
            )
            .filter(Paper.source_url.is_not(None))
            .order_by(Paper.published_date.desc())
            .limit(limit)
            .all()
        )

        print(f"[*] {len(rows)} articles sans cover dans les {days} derniers jours (limite {limit})")
        updated = 0
        for i, p in enumerate(rows, 1):
            if not p.source_url:
                continue
            cover = fetch_cover_image(p.source_url)
            if cover:
                p.image_url = cover
                db.add(p)
                updated += 1
                print(f"    [{i}/{len(rows)}] OK  {p.source} | {p.title[:60]} | {cover[:80]}")
            else:
                print(f"    [{i}/{len(rows)}] --  {p.source} | {p.title[:60]} (aucune image)")
            if i % 10 == 0:
                db.commit()
                print(f"    ... commit intermédiaire ({i}/{len(rows)})")
            time.sleep(delay)
        db.commit()
        print(f"\n[=] Backfill terminé : {updated}/{len(rows)} covers trouvées et enregistrées")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="fenêtre de jours en arrière")
    ap.add_argument("--limit", type=int, default=200, help="nombre max d'articles à traiter")
    ap.add_argument("--delay", type=float, default=0.3, help="délai entre deux requêtes (s)")
    args = ap.parse_args()
    run(days=args.days, limit=args.limit, delay=args.delay)
