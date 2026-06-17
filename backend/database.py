from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

SQLALCHEMY_DATABASE_URL = "sqlite:///./research.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
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
        "analyzed_at": "DATETIME",
    }
    with engine.begin() as conn:
        for name, coltype in new_columns.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE papers ADD COLUMN {name} {coltype}"))
