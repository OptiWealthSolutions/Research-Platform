"""Generate a one-page PDF "compte rendu" for a paper using fpdf2."""
from datetime import datetime

from fpdf import FPDF

NAVY = (3, 7, 18)
BLUE = (37, 99, 235)
GREY = (120, 130, 145)
DARK = (20, 28, 40)

_SENTIMENT_COLOR = {
    "positive": (22, 163, 74),
    "negative": (220, 38, 38),
    "neutral": (148, 163, 184),
}


def _latin1(s) -> str:
    """fpdf2 core fonts are latin-1 only; sanitize anything else."""
    if not s:
        return ""
    s = str(s)
    replacements = {"‘": "'", "’": "'", "“": '"', "”": '"',
                    "–": "-", "—": "-", "…": "...", " ": " "}
    for a, b in replacements.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


def build_report(paper) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    epw = pdf.w - pdf.l_margin - pdf.r_margin

    # Header band
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, pdf.w, 26, style="F")
    pdf.set_xy(pdf.l_margin, 8)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Courier", "B", 13)
    pdf.cell(0, 8, "MACRO RESEARCH TERMINAL  /  COMPTE RENDU", ln=1)
    pdf.ln(10)

    # Meta line
    pdf.set_text_color(*GREY)
    pdf.set_font("Helvetica", "", 9)
    date_str = paper.published_date.strftime("%d %b %Y") if paper.published_date else "Unknown date"
    pdf.cell(0, 5, _latin1(f"{paper.source or 'Unknown'}  -  {date_str}"), ln=1)
    pdf.ln(1)

    # Title
    pdf.set_text_color(*DARK)
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(epw, 7, _latin1(paper.title))
    pdf.ln(1)

    # Authors
    if paper.authors:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(*GREY)
        pdf.multi_cell(epw, 5, _latin1(paper.authors))
    pdf.ln(3)

    # Sentiment block
    if paper.sentiment_label:
        color = _SENTIMENT_COLOR.get(paper.sentiment_label, GREY)
        pdf.set_fill_color(*color)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Courier", "B", 10)
        score = paper.sentiment_score if paper.sentiment_score is not None else 0.0
        label = f" SENTIMENT: {paper.sentiment_label.upper()}  ({score:+.2f}) "
        pdf.cell(pdf.get_string_width(label) + 6, 7, _latin1(label), fill=True, ln=1)
        pdf.ln(2)
        if paper.sentiment_detail:
            pdf.set_text_color(*GREY)
            pdf.set_font("Courier", "", 8)
            d = paper.sentiment_detail
            det = "  ".join(f"{k}={v:.2f}" for k, v in d.items())
            pdf.cell(0, 4, _latin1(det), ln=1)
        pdf.ln(2)

    # Keywords
    if paper.keywords:
        pdf.set_text_color(*BLUE)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, "KEY TERMS", ln=1)
        pdf.set_text_color(*DARK)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(epw, 5, _latin1("  -  ".join(paper.keywords)))
        pdf.ln(2)

    # Summary
    if paper.summary:
        pdf.set_text_color(*BLUE)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, "SUMMARY", ln=1)
        pdf.set_text_color(*DARK)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(epw, 5, _latin1(paper.summary))
        pdf.ln(2)

    # Abstract
    if paper.abstract:
        pdf.set_text_color(*BLUE)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, "ABSTRACT", ln=1)
        pdf.set_text_color(*GREY)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(epw, 4.5, _latin1(paper.abstract))
        pdf.ln(2)

    # Source link
    link = paper.source_url or paper.pdf_url
    if link:
        pdf.set_text_color(*BLUE)
        pdf.set_font("Helvetica", "U", 9)
        pdf.multi_cell(epw, 5, _latin1(link), link=link)

    # Footer
    pdf.set_y(-15)
    pdf.set_text_color(*GREY)
    pdf.set_font("Courier", "", 7)
    pdf.cell(0, 5, _latin1(f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} - Macro Research Terminal"), align="C")

    out = pdf.output()
    return bytes(out)
