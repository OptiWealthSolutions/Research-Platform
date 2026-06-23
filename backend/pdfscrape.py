"""Find and fetch the full-article PDF a bank publishes for a research note.

Many desks expose a "Download PDF" on the article page (ING:
think.ing.com/downloads/pdf/...). We resolve that link from the landing page,
cache it on the paper, and the feed preview embeds the real PDF. Also exposes
the extracted text (pypdf) for downstream use.
"""
import io
import re
from urllib.parse import urljoin

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

# href patterns that indicate a downloadable article PDF
_PDF_HREF = re.compile(r"(/downloads/pdf/|/download/pdf|\.pdf($|\?)|/pdf/[\w-]+/?$)", re.I)


def find_pdf_url(source_url, *, timeout=15):
    """Scrape an article landing page for its PDF download link. Returns an
    absolute URL or None."""
    if not source_url:
        return None
    try:
        from bs4 import BeautifulSoup
        r = requests.get(source_url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "lxml")
    except Exception:
        return None
    # 1) anchors whose href looks like a PDF
    for a in soup.find_all("a", href=True):
        if _PDF_HREF.search(a["href"]):
            return urljoin(source_url, a["href"])
    # 2) anchors labelled "download" pointing somewhere plausible
    for a in soup.find_all("a", href=True):
        label = (a.get_text(" ", strip=True) or "").lower()
        if "download" in label and ("pdf" in label or "/pdf" in a["href"].lower()):
            return urljoin(source_url, a["href"])
    return None


def resolve(paper, *, timeout=15):
    """Return a usable PDF url for the paper, preferring a stored one, else
    scraping the landing page. Does not persist; caller may cache."""
    if paper.pdf_url:
        return paper.pdf_url
    return find_pdf_url(paper.source_url, timeout=timeout)


def fetch_text(url, *, timeout=25, max_pages=30):
    """Download a PDF and extract its text (best-effort)."""
    try:
        import pypdf
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        if "pdf" not in r.headers.get("Content-Type", "").lower() and not r.content[:4] == b"%PDF":
            return ""
        reader = pypdf.PdfReader(io.BytesIO(r.content))
        return " ".join((p.extract_text() or "") for p in reader.pages[:max_pages])
    except Exception:
        return ""
