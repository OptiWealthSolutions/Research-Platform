"""Extract published FX *calls* from bank / fund research.

Honest scope: desks' structured trade tickets (entry / take-profit / stop) live
behind login walls and are a licensed data product — we do NOT fabricate price
levels. What IS public in the research we hold is the *directional stance*: which
currency/pair a note is about and whether the desk's tone is bullish / bearish.
This module turns each bank/fund note that names a currency into one or more
"calls" (institution · date · pair · bias · thesis) — the real, non-fabricated
half of a trade-ideas board.
"""
import re

# Tradable currencies we recognise, with a default counter for singles.
_CCY = ["EUR", "USD", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "CNY", "CNH",
        "SEK", "NOK", "MXN", "INR"]
_CCY_SET = set(_CCY)
# currency words -> code (for titles that say "dollar" not "USD")
_WORDS = {
    "dollar": "USD", "greenback": "USD", "euro": "EUR", "sterling": "GBP",
    "pound": "GBP", "cable": "GBP", "yen": "JPY", "franc": "CHF",
    "aussie": "AUD", "kiwi": "NZD", "loonie": "CAD", "yuan": "CNY",
    "renminbi": "CNY", "krona": "SEK", "krone": "NOK", "rupee": "INR", "peso": "MXN",
}
# country / region words -> the currency a note about them bears on
_COUNTRY = {
    "united states": "USD", "u.s.": "USD", "america": "USD", "american": "USD",
    "fed": "USD", "treasury": "USD", "euro area": "EUR", "eurozone": "EUR",
    "europe": "EUR", "european": "EUR", "germany": "EUR", "german": "EUR",
    "france": "EUR", "french": "EUR", "italy": "EUR", "italian": "EUR",
    "spain": "EUR", "belgian": "EUR", "belgium": "EUR", "ecb": "EUR",
    "united kingdom": "GBP", "britain": "GBP", "british": "GBP", "boe": "GBP",
    "japan": "JPY", "japanese": "JPY", "boj": "JPY", "tokyo": "JPY",
    "switzerland": "CHF", "swiss": "CHF", "snb": "CHF",
    "canada": "CAD", "canadian": "CAD", "australia": "AUD", "australian": "AUD",
    "new zealand": "NZD", "china": "CNY", "chinese": "CNY", "pboc": "CNY",
    "norway": "NOK", "norwegian": "NOK", "sweden": "SEK", "swedish": "SEK",
    "india": "INR", "indian": "INR", "mexico": "MXN",
}
_PAIR_RE = re.compile(r"\b(" + "|".join(_CCY) + r")\s*[/\-]?\s*(" + "|".join(_CCY) + r")\b")
_SINGLE_RE = re.compile(r"\b(" + "|".join(_CCY) + r")\b")
_WORD_RE = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.I)
_COUNTRY_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in _COUNTRY) + r")\b", re.I)
_DXY_RE = re.compile(r"\b(dxy|dollar index|broad dollar)\b", re.I)

# directional cues that override / sharpen the FinBERT tone
_BULL = re.compile(r"\b(bullish|rally|rallies|surge|soar|stronger|strength|gains?|"
                   r"upside|appreciat\w+|firm\w*|outperform|overweight|buy|long\b|higher|hawkish)\b", re.I)
_BEAR = re.compile(r"\b(bearish|sell-?off|slump|plunge|weaker|weakness|fall\w*|drop\w*|"
                   r"downside|depreciat\w+|soft\w*|underperform|underweight|sell|short\b|lower|dovish)\b", re.I)


def _norm_pair(a, b):
    if a == b:
        return None
    return f"{a}/{b}"


def _bias(text, sentiment_label):
    """bullish | bearish | neutral, from explicit cues, FinBERT as fallback."""
    nb = len(_BULL.findall(text))
    ns = len(_BEAR.findall(text))
    if nb or ns:
        if nb > ns:
            return "bullish"
        if ns > nb:
            return "bearish"
    if sentiment_label == "positive":
        return "bullish"
    if sentiment_label == "negative":
        return "bearish"
    return "neutral"


def extract_calls(paper, *, max_pairs=2):
    """Return a list of call dicts for one paper, or [] if it names no currency.

    Each call: {pair, bias}. Caller adds institution/date/source/etc.
    """
    title = paper.title or ""
    text = f"{title} {paper.abstract or ''}"
    bias = _bias(text, paper.sentiment_label)

    pairs = []
    seen = set()

    def add(p):
        if p and p not in seen:
            seen.add(p)
            pairs.append(p)

    # 1) currency pairs the analysis layer already derived (most reliable)
    for p in (paper.currency_pairs or []):
        add(p)

    # 2) explicit pairs written in the text (EUR/USD, EURUSD, EUR-USD)
    for a, b in _PAIR_RE.findall(text):
        if a in _CCY_SET and b in _CCY_SET:
            add(_norm_pair(a, b))

    # 3) single currencies / currency words -> a representative pair
    if len(pairs) < max_pairs:
        singles = []
        for c in _SINGLE_RE.findall(text):
            if c not in singles:
                singles.append(c)
        for w in _WORD_RE.findall(text):
            c = _WORDS[w.lower()]
            if c not in singles:
                singles.append(c)
        for w in _COUNTRY_RE.findall(text):
            c = _COUNTRY[w.lower()]
            if c not in singles:
                singles.append(c)
        if _DXY_RE.search(text) and "USD" not in singles:
            singles.insert(0, "USD")
        for c in singles:
            if len(pairs) >= max_pairs:
                break
            add("DXY" if c == "USD" else f"{c}/USD")

    return [{"pair": p, "bias": bias} for p in pairs[:max_pairs]]
