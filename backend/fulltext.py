"""Full text of a paper, persisted on the row (`full_text`, `text_source`).

Deepest source first: direct PDF -> PDF linked from the landing page ->
article body of the landing page (HTML) -> title + abstract. Every outbound
request goes through `netguard` (SSRF). Feeds give 375 characters of abstract
on average; a note quoted in a thesis needs the body.
"""
from __future__ import annotations

import io
import re
from urllib.parse import urljoin

from . import netguard
from .timeutil import utcnow

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/pdf,*/*",
}
MAX_CHARS = 200_000
_PLACEHOLDER = {"click link for abstract.", "no abstract available.", "", "untitled"}


def _pdf_text(content: bytes, max_pages: int = 40) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(content))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages])
    except Exception:
        return ""


def _get(url: str, timeout: int = 25):
    try:
        r = netguard.safe_get(url, headers=HEADERS, timeout=timeout)
    except Exception:
        return None
    if r.status_code in (403, 429):
        try:
            netguard.check_url(url)
            r2 = netguard.impersonated_get(url, headers=HEADERS, timeout=timeout)
            if r2 is not None and r2.status_code == 200:
                return r2
        except Exception:
            return None
    return r if r.status_code == 200 else None


def _is_pdf(r) -> bool:
    return "pdf" in (r.headers.get("Content-Type", "") or "").lower() or r.content[:4] == b"%PDF"


def html_article_text(html: bytes | str) -> str:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg"]):
        t.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    paras = []
    for el in root.find_all(["p", "li", "h2", "h3", "blockquote"]):
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if len(txt) >= 40 or (el.name in ("h2", "h3") and len(txt) >= 8):
            paras.append(txt)
    seen, out = set(), []
    for p in paras:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return "\n".join(out)


def fetch(paper, timeout: int = 25) -> tuple[str, str]:
    """Return (text, source) without touching the DB."""
    if paper.pdf_url:
        r = _get(paper.pdf_url, timeout=timeout)
        if r is not None and _is_pdf(r):
            t = _pdf_text(r.content)
            if len(t) > 400:
                return t[:MAX_CHARS], "pdf"
    if paper.source_url:
        r = _get(paper.source_url, timeout=timeout)
        if r is not None:
            if _is_pdf(r):
                t = _pdf_text(r.content)
                if len(t) > 400:
                    return t[:MAX_CHARS], "pdf"
            else:
                html = r.text
                for href in re.findall(r'href=["\']([^"\']+?\.pdf(?:\?[^"\']*)?)["\']', html, re.I)[:3]:
                    rp = _get(urljoin(paper.source_url, href), timeout=timeout)
                    if rp is not None and _is_pdf(rp):
                        t = _pdf_text(rp.content)
                        if len(t) > 400:
                            return t[:MAX_CHARS], "pdf"
                t = html_article_text(html)
                if len(t) > 400:
                    return t[:MAX_CHARS], "html"
    abstract = (paper.abstract or "").strip()
    if abstract.lower() in _PLACEHOLDER:
        abstract = ""
    return f"{paper.title}. {abstract}".strip(), "abstract"


def ensure(paper, *, refresh: bool = False, timeout: int = 25) -> tuple[str, str]:
    """Fetch once and persist on the row; caller commits."""
    if paper.full_text and paper.text_source in ("pdf", "html") and not refresh:
        return paper.full_text, paper.text_source
    text, src = fetch(paper, timeout=timeout)
    paper.full_text = text
    paper.text_source = src
    paper.text_fetched_at = utcnow()
    return text, src
