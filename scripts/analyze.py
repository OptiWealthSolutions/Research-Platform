"""Run FinBERT sentiment + keyword + summary analysis on recent papers.

Run:  /opt/anaconda3/bin/python3 scripts/analyze.py [--days N] [--all]
"""
import sys
import os
import argparse
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.database import SessionLocal, engine, run_migrations  # noqa: E402
from backend.models import Base, Paper  # noqa: E402
from backend import analysis  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="lookback window in days")
    ap.add_argument("--all", action="store_true", help="(re)analyze every paper")
    args = ap.parse_args()

    Base.metadata.create_all(bind=engine)
    run_migrations()
    db = SessionLocal()
    try:
        if args.all:
            papers = db.query(Paper).order_by(Paper.published_date.desc()).all()
            print(f"[*] analyzing ALL {len(papers)} papers...")
            done = 0
            for p in papers:
                try:
                    analysis.analyze_paper(p)
                    db.commit()
                    done += 1
                    print(f"    [+] {p.sentiment_label:>8} ({p.sentiment_score:+.2f})  {p.title[:70]}")
                except Exception as e:
                    db.rollback()
                    print(f"    [!] {p.id}: {e}")
            print(f"[=] analyzed {done} papers")
        else:
            print(f"[*] analyzing papers <= {args.days} days old...")
            n = analysis.analyze_recent(db, days=args.days)
            print(f"[=] analyzed {n} papers")
    finally:
        db.close()


if __name__ == "__main__":
    print(f"[{datetime.now()}] ANALYZE START")
    main()
    print(f"[{datetime.now()}] ANALYZE DONE")
