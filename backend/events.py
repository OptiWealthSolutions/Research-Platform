"""Macro event calendar from the ForexFactory weekly feed.

Replaces the calendar typed by hand in main.py/mcp.py (stale after
2026-12-18, CPI dates guessed). Source: nfs.faireconomy.media weekly JSON
(the feed behind forexfactory.com's calendar), cached one hour. No fallback
list: if the feed is unreachable the endpoint says so.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .timeutil import utcnow

FF_URLS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
           "https://nfs.faireconomy.media/ff_calendar_nextweek.json"]
_CACHE: dict = {"t": 0.0, "rows": [], "error": None}
TTL = 3600
DISK = Path(__file__).resolve().parents[1] / ".events_cache.json"

REGION = {"USD": "US", "EUR": "EU", "GBP": "UK", "JPY": "Japan", "CHF": "Switzerland",
          "CAD": "Canada", "AUD": "Australia", "NZD": "New Zealand", "CNY": "China"}
_THEMES = [
    ("Monetary Policy", ("rate", "monetary", "fomc", "minutes", "press conference", "policy")),
    ("Inflation", ("cpi", "ppi", "pce", "inflation", "price")),
    ("Labor Market", ("employment", "payroll", "jobless", "unemployment", "claims", "earnings", "wage")),
    ("Macro-Finance", ("gdp", "pmi", "retail", "sales", "production", "trade", "confidence", "sentiment")),
]


def _theme(title: str) -> str:
    t = title.lower()
    for name, kws in _THEMES:
        if any(k in t for k in kws):
            return name
    return "Macro-Finance"


def _disk_load() -> dict:
    try:
        return json.loads(DISK.read_text())
    except Exception:
        return {}


def fetch() -> tuple[list[dict], str | None]:
    """Weekly feeds, cached 1 h in memory and kept on disk per URL: the feed rate-limits
    (HTTP 429) and the next-week file only appears late in the week, so the last good copy
    of each file is reused (past events are filtered out by `upcoming`)."""
    if time.time() - _CACHE["t"] < TTL and (_CACHE["rows"] or _CACHE["error"]):
        return _CACHE["rows"], _CACHE["error"]
    disk = _disk_load()
    rows, errs = [], []
    for url in FF_URLS:
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 404:         # next-week file appears late in the week
                rows.extend((disk.get(url) or {}).get("rows", []))
                continue
            r.raise_for_status()
            got = r.json()
            disk[url] = {"fetched_utc": utcnow().isoformat() + "Z", "rows": got}
            rows.extend(got)
        except Exception as e:
            old = disk.get(url) or {}
            rows.extend(old.get("rows", []))
            errs.append(f"{url.rsplit('/', 1)[-1]}: {type(e).__name__}: {str(e)[:60]}"
                        + (f" (copie du {old['fetched_utc'][:16]} utilisée)" if old.get("rows") else ""))
    try:
        DISK.write_text(json.dumps(disk))
    except OSError:
        pass
    seen, uniq = set(), []
    for e in rows:                           # this/next week files overlap on Sunday
        k = (e.get("date"), e.get("country"), e.get("title"))
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    err = "; ".join(errs) or None
    _CACHE.update(t=time.time() if not errs else time.time() - TTL + 600, rows=uniq, error=err)
    return uniq, err


def upcoming(limit: int = 12, impacts=("High",), currencies=None) -> tuple[list[dict], str | None]:
    rows, err = fetch()
    now = datetime.now(timezone.utc)
    today = utcnow().date()
    out = []
    for e in rows:
        try:
            when = datetime.fromisoformat(e["date"])
        except Exception:
            continue
        ccy = e.get("country", "")
        if impacts and e.get("impact") not in impacts:
            continue
        if currencies and ccy not in currencies:
            continue
        if when < now:
            continue
        w = when.astimezone(timezone.utc)
        out.append({
            "date": w.date().isoformat(), "time_utc": w.strftime("%H:%M"),
            "days_until": (w.date() - today).days, "name": e.get("title", ""), "bloc": ccy,
            "region": REGION.get(ccy, "Global"),
            "importance": "high" if e.get("impact") == "High" else "medium",
            "theme": _theme(e.get("title", "")), "forecast": e.get("forecast") or None,
            "previous": e.get("previous") or None, "source": "ForexFactory",
        })
    out.sort(key=lambda x: (x["date"], x["time_utc"]))
    return out[:limit], err
