"""Catch-up / recompute of macro facts (backend/macrofacts.py).

Run:  /opt/anaconda3/bin/python3 scripts/macro_facts.py --days 14 [--force] [--no-llm] [--llm-cap N]

Without --force only papers with no facts computed yet (or re-analysed since)
are processed; --force recomputes every paper of the window. The ingestion pass
calls the same function on recent papers. Uses RP_DB_PATH like the rest.
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.database import SessionLocal, engine, run_migrations  # noqa: E402
from backend.models import Base  # noqa: E402
from backend import macrofacts  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="lookback window in days (published date)")
    ap.add_argument("--force", action="store_true", help="recompute papers that already have facts")
    ap.add_argument("--no-llm", action="store_true", help="rules only (skip the LFM-3 pass)")
    ap.add_argument("--llm-cap", type=int, default=None, help="max LLM calls (default MF_MAX_LLM_PER_RUN or 15)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    Base.metadata.create_all(bind=engine)
    run_migrations()
    db = SessionLocal()
    try:
        stats = macrofacts.extract_recent(db, days=args.days, force=args.force, use_llm=not args.no_llm,
                                          llm_cap=args.llm_cap, verbose=args.verbose)
        print(f"[=] {stats}")
    finally:
        db.close()


if __name__ == "__main__":
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] MACRO FACTS START")
    main()
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] MACRO FACTS DONE")
