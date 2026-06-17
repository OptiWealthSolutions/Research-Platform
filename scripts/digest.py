"""Daily watchlist digest: net FinBERT sentiment over saved papers.
Prints to stdout, and emails it when SMTP env vars are configured.

Reads watchlist ids from watchlist.json (written by the app's Save button).
Falls back to the week's top-interest papers if the watchlist is empty.

Email setup (optional):
  export SMTP_HOST=smtp.gmail.com SMTP_PORT=587 \
         SMTP_USER=you@gmail.com SMTP_PASS=app_password \
         DIGEST_TO=you@gmail.com
Cron example (08:00 daily):
  0 8 * * * /opt/anaconda3/bin/python3 /path/scripts/digest.py >> digest_log.txt 2>&1
"""
import os
import sys
import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.database import SessionLocal, engine, run_migrations  # noqa: E402
from backend.models import Base, Paper  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(__file__))
BLOCS = [("USD", "US"), ("EUR", "EU"), ("GBP", "UK"), ("JPY", "Japan"), ("CNY", "China")]


def load_watchlist_ids():
    try:
        with open(os.path.join(ROOT, "watchlist.json")) as f:
            return json.load(f).get("ids", [])
    except Exception:
        return []


def build(db):
    ids = load_watchlist_ids()
    if ids:
        papers = db.query(Paper).filter(Paper.id.in_(ids)).all()
        scope = f"watchlist ({len(papers)} saved)"
    else:
        cut = datetime.now() - timedelta(days=7)
        papers = (
            db.query(Paper)
            .filter(Paper.published_date >= cut, Paper.published_date <= datetime.now())
            .filter(Paper.interest_score.isnot(None))
            .order_by(Paper.interest_score.desc(), Paper.published_date.desc())
            .limit(15).all()
        )
        scope = "top interest, last 7 days (watchlist empty)"

    scored = [p for p in papers if p.sentiment_score is not None]
    net = sum(p.sentiment_score for p in scored) / len(scored) if scored else 0.0
    label = "BULLISH" if net > 0.03 else "BEARISH" if net < -0.03 else "NEUTRAL"

    lines = [
        "MACRO RESEARCH TERMINAL - DAILY DIGEST",
        datetime.now().strftime("%A %d %B %Y, %H:%M"),
        f"Scope: {scope}",
        "",
        f"NET BIAS: {label}  ({net:+.2f})   "
        f"[+{sum(1 for p in scored if p.sentiment_label=='positive')} "
        f"={sum(1 for p in scored if p.sentiment_label=='neutral')} "
        f"-{sum(1 for p in scored if p.sentiment_label=='negative')}]",
        "",
    ]

    bloc_acc = {}
    for p in scored:
        for code, region in BLOCS:
            if region in (p.country_tags or []):
                bloc_acc.setdefault(code, []).append(p.sentiment_score)
    if bloc_acc:
        lines.append("BY BLOC:")
        for code, v in sorted(bloc_acc.items(), key=lambda kv: sum(kv[1]) / len(kv[1])):
            lines.append(f"  {code}: {sum(v)/len(v):+.2f}  ({len(v)} docs)")
        lines.append("")

    ranked = sorted(scored, key=lambda p: p.sentiment_score, reverse=True)
    if ranked:
        lines.append("TOP BULLISH:")
        for p in ranked[:3]:
            lines.append(f"  {p.sentiment_score:+.2f}  [{p.source}] {p.title[:70]}")
        lines.append("TOP BEARISH:")
        for p in ranked[-3:][::-1]:
            lines.append(f"  {p.sentiment_score:+.2f}  [{p.source}] {p.title[:70]}")
    return "\n".join(lines)


def maybe_email(body):
    host = os.environ.get("SMTP_HOST")
    to = os.environ.get("DIGEST_TO")
    if not (host and to):
        print("\n[i] SMTP not configured (set SMTP_HOST/SMTP_USER/SMTP_PASS/DIGEST_TO to email).")
        return
    msg = MIMEText(body)
    msg["Subject"] = "Macro Research Digest - " + datetime.now().strftime("%d %b %Y")
    msg["From"] = os.environ.get("SMTP_USER", to)
    msg["To"] = to
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
            s.sendmail(msg["From"], [to], msg.as_string())
        print(f"\n[=] Digest emailed to {to}")
    except Exception as e:
        print(f"\n[!] Email failed: {e}")


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    run_migrations()
    db = SessionLocal()
    try:
        body = build(db)
        print(body)
        maybe_email(body)
    finally:
        db.close()
