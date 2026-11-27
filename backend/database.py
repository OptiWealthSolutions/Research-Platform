import os

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

# Absolute path: the DB no longer depends on the working directory of whoever
# imports this module (uvicorn, scheduler, scripts, tests).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("RP_DB_PATH", os.path.join(ROOT, "research.db"))
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 30}
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):
    # WAL: readers (API, MCP) never block on the ingest writer; busy_timeout
    # absorbs the short write locks instead of raising "database is locked".
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations():
    """Lightweight additive migration: add new columns to an existing
    SQLite `papers` table without dropping data. Safe to run repeatedly."""
    inspector = inspect(engine)
    if "papers" not in inspector.get_table_names():
        return  # create_all will build the full schema
    existing = {c["name"] for c in inspector.get_columns("papers")}
    new_columns = {
        "currency_pairs": "JSON",
        "created_at": "DATETIME",
        "summary": "TEXT",
        "keywords": "JSON",
        "sentiment_label": "VARCHAR",
        "sentiment_score": "FLOAT",
        "sentiment_detail": "JSON",
        "interest_score": "INTEGER",
        "impact_score": "FLOAT",
        "analyzed_at": "DATETIME",
        "thesis": "VARCHAR",
        "thesis_tags": "JSON",
        "area": "JSON",
        "image_url": "VARCHAR",
        "published_precision": "VARCHAR",
        "full_text": "TEXT",
        "text_source": "VARCHAR",
        "text_fetched_at": "DATETIME",
        "doc_kind": "VARCHAR",
        "analysis_tier": "VARCHAR",
        "analysis_engine": "VARCHAR",
        "relevance_score": "FLOAT",
        "content_depth": "VARCHAR",
        "asset_impacts": "JSON",
        "quant_taxonomy": "JSON",
        "macro_facts_at": "DATETIME",
    }
    added = []
    with engine.begin() as conn:
        for name, coltype in new_columns.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE papers ADD COLUMN {name} {coltype}"))
                added.append(name)
        if "published_precision" in added:
            # Rows ingested before 2026-11-13 had their time of day overwritten
            # with the Paris ingest clock: only their calendar date is true.
            conn.execute(text("UPDATE papers SET published_precision='date'"))
            conn.execute(text(
                "UPDATE papers SET published_precision='suspect' "
                "WHERE published_date > datetime('now', '+1 day')"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_papers_source_pub ON papers (source, published_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_papers_analyzed ON papers (analyzed_at)"))
    # macro_facts (backend/macrofacts.py): create_all builds the table; later
    # columns are added here the same additive way.
    if "macro_facts" in inspect(engine).get_table_names():
        have = {c["name"] for c in inspect(engine).get_columns("macro_facts")}
        with engine.begin() as conn:
            for name, coltype in {"event_id": "VARCHAR", "horizon": "VARCHAR"}.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE macro_facts ADD COLUMN {name} {coltype}"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_macro_facts_zone_ind "
                              "ON macro_facts (zone, indicator, published_date)"))
