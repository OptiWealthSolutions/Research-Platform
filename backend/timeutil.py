"""Single time convention for the platform.

Every timestamp stored in `research.db` (published_date, created_at,
analyzed_at, text_fetched_at) is **naive UTC**. Feeds give UTC structs
(feedparser `*_parsed`) or tz-aware strings; both are converted to UTC before
the tz is dropped. Comparisons ("not in the future", "last N days") use
`utcnow()` so a Paris-time clock never leaks into the data.

`published_precision`:
  * "datetime" -> the feed carried a real time of day (UTC);
  * "date"     -> only the calendar date is known; the item is treated as
                  available at the END of that UTC day (point-in-time safe);
  * "seen"     -> no publication date (undated listing item): the ingest
                  instant is the only true bound;
  * "suspect"  -> the feed date was in the future; the row is kept for search
                  but never used as point-in-time evidence.
"""
from datetime import datetime, timezone, timedelta


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(d: datetime) -> datetime:
    if d.tzinfo is not None:
        return d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


def available_at(published: datetime | None, precision: str | None,
                 created_at: datetime | None = None) -> datetime | None:
    """Earliest instant (naive UTC) at which the paper is known to exist.

    * "datetime": the publication instant;
    * "date": end of the publication day, or the ingest instant if earlier
      (the row was in the database at `created_at`, so it existed then);
    * "seen": the ingest instant (first time the item was seen);
    * "suspect": None (never point-in-time evidence).
    Rows ingested before 2026-11-13 have `created_at` in Paris time, i.e. one
    or two hours LATE as UTC: the bound stays conservative."""
    if precision == "suspect":
        return None
    if precision == "datetime" and published is not None:
        return published
    if precision == "seen":
        return created_at or published
    if published is None:
        return created_at
    eod = datetime(published.year, published.month, published.day) + timedelta(days=1)
    return min(eod, created_at) if created_at and created_at >= published else eod


def iso_z(d: datetime | None) -> str | None:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None
