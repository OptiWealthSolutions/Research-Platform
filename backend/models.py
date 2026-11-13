import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, JSON, Float, Integer
from .database import Base


class Paper(Base):
    __tablename__ = "papers"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=False)
    authors = Column(String, nullable=True)  # Comma-separated or JSON
    abstract = Column(Text, nullable=True)
    published_date = Column(DateTime, index=True)
    source = Column(String, index=True)
    country_tags = Column(JSON, default=[])   # JSON array of strings
    thematic_tags = Column(JSON, default=[])  # JSON array of strings
    currency_pairs = Column(JSON, default=[]) # FX pairs the paper bears on
    created_at = Column(DateTime, default=datetime.now, index=True)  # ingest time (Mac local time)
    pdf_url = Column(String, nullable=True)
    source_url = Column(String, nullable=True)  # Direct link to the landing page
    image_url = Column(String, nullable=True)   # og:image / cover extracted from source
    doi = Column(String, unique=True, index=True, nullable=True)
    clean_title_hash = Column(String, unique=True, index=True, nullable=False)

    # --- Trader-focused analysis layer (FinBERT + NLP) ---
    summary = Column(Text, nullable=True)              # "compte rendu" extractive summary
    keywords = Column(JSON, default=[])                # extracted key terms
    sentiment_label = Column(String, nullable=True)    # positive / negative / neutral
    sentiment_score = Column(Float, nullable=True)     # signed score in [-1, 1]
    sentiment_detail = Column(JSON, default={})        # {positive, negative, neutral}
    interest_score = Column(Integer, nullable=True)    # trader value index 1..5
    impact_score = Column(Float, nullable=True)        # macro impact 0..100 (conviction x source tier x theme)
    analyzed_at = Column(DateTime, nullable=True)      # when the analysis ran

    # --- Research thesis linkage (macro research hub) ---
    thesis = Column(String, nullable=True)             # free-text thesis this paper supports
    thesis_tags = Column(JSON, default=[])             # tags linking this paper to existing hub theses

    # --- Multi-asset / Area (macro research hub) ---
    area = Column(JSON, default=[])                    # monetary zones impacted (USD, EUR, GBP…)
