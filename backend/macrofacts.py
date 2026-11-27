"""Numeric macro facts quoted in the research we hold (Horacle axis D2).

`fxcalls.py` gives the directional half (institution · pair · bias, no price
level). This module gives the numeric half: published prints, bank forecasts,
consensus and rate decisions, each tied to the exact sentence that states it.

A fact = zone · geo · indicator · kind · value · unit · period (· horizon) ·
quote, plus where it was found (paper, source, institution, date) and how
(`method` regex | llm, `confidence`).

Golden rule: a fact whose `quote` (an exact excerpt of the article, <= 200
characters) does not contain `value` literally is rejected. No number is ever
derived, rounded or converted: 119,000 stays 119000 (unit `jobs`), 119K stays
119 (unit `k`).

Two passes:
  1. rules (regex + indicator / zone dictionaries) on every paper;
  2. local LLM (LFM-3 via Ollama, capped per run like EP_MAX_LLM_PER_RUN) for
     high-impact papers where the rules found nothing; strict JSON, schema
     validated, then the same golden rule and "quote is an exact excerpt".

Zones are the currency codes of `fxcalls._CCY` (same as `papers.area`); `geo`
is the statistical area inside the zone (EUR: EA, DE, FR, ES, IT…; JPY: JP and
JP-TKY for Tokyo). The unique key is (paper_id, zone, geo, indicator, kind,
period): `geo` is in it so a German and a French print quoted in the same
article do not overwrite each other.
"""
from __future__ import annotations

import html
import json
import os
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import fxcalls
from .events import event_id
from . import sources as catalog
from .timeutil import iso_z, utcnow

# ---------------------------------------------------------------------------
# closed vocabularies
# ---------------------------------------------------------------------------
# indicator -> (family, default unit, description). Extend by hand.
INDICATORS: dict[str, tuple[str, str, str]] = {
    "cpi_yoy": ("inflation", "%", "Headline CPI / inflation, year on year (euro area: HICP)"),
    "cpi_mom": ("inflation", "%", "Headline CPI, month on month"),
    "core_cpi_yoy": ("inflation", "%", "Core CPI, year on year (Japan: ex fresh food)"),
    "core_cpi_mom": ("inflation", "%", "Core CPI, month on month"),
    "hicp_yoy": ("inflation", "%", "National HICP of a euro-area member, year on year"),
    "pce_yoy": ("inflation", "%", "US PCE price index, year on year"),
    "core_pce_yoy": ("inflation", "%", "US core PCE price index, year on year"),
    "core_pce_mom": ("inflation", "%", "US core PCE price index, month on month"),
    "gdp_qoq": ("growth", "%", "Real GDP, quarter on quarter (not annualised)"),
    "gdp_qoq_saar": ("growth", "%", "Real GDP, quarter on quarter annualised (US convention)"),
    "gdp_yoy": ("growth", "%", "Real GDP, year on year"),
    "gdp_annual": ("growth", "%", "Real GDP growth over a calendar year (forecasts)"),
    "unemployment": ("employment", "%", "Unemployment rate"),
    "unemployment_change": ("employment", "k", "Change in the number of unemployed"),
    "nfp": ("employment", "k", "US non-farm payrolls, monthly change"),
    "employment_change": ("employment", "k", "Employment change (non-US)"),
    "jobless_claims": ("employment", "k", "Initial jobless claims, weekly"),
    "wages_yoy": ("employment", "%", "Average earnings / wages, year on year"),
    "pmi_manufacturing": ("activity", "index", "Manufacturing PMI (ISM in the US)"),
    "pmi_services": ("activity", "index", "Services PMI"),
    "pmi_composite": ("activity", "index", "Composite PMI"),
    "retail_sales_mom": ("activity", "%", "Retail sales, month on month"),
    "retail_sales_yoy": ("activity", "%", "Retail sales, year on year"),
    "policy_rate": ("policy_rate", "%", "Central-bank policy rate level (upper bound of a range)"),
    "rate_hike_bp": ("policy_rate", "bp", "Size of a policy-rate hike"),
    "rate_cut_bp": ("policy_rate", "bp", "Size of a policy-rate cut"),
}
KINDS = ("actual", "forecast", "consensus", "previous", "decision")
ZONES = tuple(fxcalls._CCY)
UNITS = ("%", "bp", "k", "jobs", "index")
FAMILIES = sorted({v[0] for v in INDICATORS.values()})

# geo -> zone. The aggregate geo of each zone is the one the board reads first.
GEO_ZONE = {
    "US": "USD", "EA": "EUR", "DE": "EUR", "FR": "EUR", "ES": "EUR", "IT": "EUR", "NL": "EUR",
    "BE": "EUR", "UK": "GBP", "JP": "JPY", "JP-TKY": "JPY", "CH": "CHF", "CA": "CAD",
    "AU": "AUD", "NZ": "NZD", "CN": "CNY", "SE": "SEK", "NO": "NOK", "IN": "INR", "MX": "MXN",
}
ZONE_AGGREGATE = {"USD": "US", "EUR": "EA", "GBP": "UK", "JPY": "JP", "CHF": "CH", "CAD": "CA",
                  "AUD": "AU", "NZD": "NZ", "CNY": "CN", "SEK": "SE", "NOK": "NO", "INR": "IN",
                  "MXN": "MX"}

# Place / institution words -> geo. A geo of None is a foreign economy outside
# our zones: it blocks attribution (a Brazilian CPI is never filed under USD).
_GEO_WORDS: dict[str, Optional[str]] = {
    "united states": "US", "u.s.": "US", "us": "US", "america": "US", "american": "US",
    "americans": "US", "fed": "US", "federal reserve": "US", "fomc": "US", "bls": "US", "bea": "US",
    "euro area": "EA", "euro-area": "EA", "eurozone": "EA", "euro zone": "EA", "euro-zone": "EA",
    "ecb": "EA", "european central bank": "EA", "eurostat": "EA",
    "germany": "DE", "german": "DE", "bundesbank": "DE", "france": "FR", "french": "FR",
    "spain": "ES", "spanish": "ES", "italy": "IT", "italian": "IT", "netherlands": "NL",
    "dutch": "NL", "belgium": "BE", "belgian": "BE",
    "united kingdom": "UK", "u.k.": "UK", "uk": "UK", "britain": "UK", "british": "UK",
    "boe": "UK", "bank of england": "UK", "ons": "UK",
    "japan": "JP", "japanese": "JP", "boj": "JP", "bank of japan": "JP", "tokyo": "JP-TKY",
    "switzerland": "CH", "swiss": "CH", "snb": "CH", "swiss national bank": "CH",
    "canada": "CA", "canadian": "CA", "boc": "CA", "bank of canada": "CA",
    "australia": "AU", "australian": "AU", "aussie": "AU", "rba": "AU", "reserve bank of australia": "AU",
    "new zealand": "NZ", "rbnz": "NZ", "reserve bank of new zealand": "NZ",
    "china": "CN", "chinese": "CN", "pboc": "CN", "people's bank of china": "CN",
    "sweden": "SE", "swedish": "SE", "riksbank": "SE", "norway": "NO", "norwegian": "NO",
    "norges bank": "NO", "india": "IN", "indian": "IN", "rbi": "IN", "reserve bank of india": "IN",
    "mexico": "MX", "mexican": "MX", "banxico": "MX",
    # foreign economies: block
    "brazil": None, "brazilian": None, "korea": None, "korean": None, "bank of korea": None,
    "zambia": None, "turkey": None, "turkish": None, "south africa": None, "poland": None,
    "polish": None, "hungary": None, "czech": None, "russia": None, "russian": None,
    "indonesia": None, "philippines": None, "thailand": None, "malaysia": None,
    "singapore": None, "taiwan": None, "vietnam": None, "chile": None, "colombia": None,
    "peru": None, "argentina": None, "israel": None, "egypt": None, "nigeria": None,
    "kenya": None, "ghana": None, "saudi": None, "hong kong": None, "denmark": None,
    "danish": None, "austria": None, "ireland": None, "portugal": None, "greece": None,
}
# central banks -> geo, for policy-rate facts (actor of the decision)
_CB_WORDS = {
    "fed": "US", "federal reserve": "US", "fomc": "US", "ecb": "EA", "european central bank": "EA",
    "boe": "UK", "bank of england": "UK", "mpc": "UK", "boj": "JP", "bank of japan": "JP",
    "snb": "CH", "swiss national bank": "CH", "boc": "CA", "bank of canada": "CA",
    "rba": "AU", "reserve bank of australia": "AU", "rbnz": "NZ",
    "reserve bank of new zealand": "NZ", "pboc": "CN", "people's bank of china": "CN",
    "riksbank": "SE", "norges bank": "NO", "rbi": "IN", "reserve bank of india": "IN",
    "banxico": "MX", "bank of korea": None, "central bank of brazil": None,
    "bank of zambia": None,
}


def _words_re(words: Iterable[str]) -> re.Pattern:
    alts = sorted(words, key=len, reverse=True)
    return re.compile(r"(?<![\w.])(" + "|".join(re.escape(w) for w in alts) + r")(?![\w])", re.I)


_GEO_RE = _words_re(_GEO_WORDS)
_CB_RE = _words_re(_CB_WORDS)
_US_RE = re.compile(r"(?<![\w.])(US|U\.S\.)(?![\w])")       # "us" only in capitals


def _geo_hits(text: str, *, central_banks: bool = False):
    """[(start, end, geo)] for place words in `text` ("us" only as US / U.S.)."""
    rx, table = (_CB_RE, _CB_WORDS) if central_banks else (_GEO_RE, _GEO_WORDS)
    out = []
    for m in rx.finditer(text):
        w = m.group(1).lower()
        if w in ("us", "u.s.") and not _US_RE.fullmatch(m.group(1)):
            continue
        if w == "uk" and m.group(1) != "UK":
            continue
        out.append((m.start(), m.end(), table[w]))
    return out


# ---------------------------------------------------------------------------
# indicator mentions
# ---------------------------------------------------------------------------
# family key -> pattern. Order matters only for overlaps (longest match wins).
# Keys starting with "x_" are blockers: sub-components / other measures whose
# numbers must not be filed under the indicator mentioned before them.
_MENTIONS = [
    ("core_pce", r"core\s+(?:pce|personal consumption expenditures?)(?:\s+price(?:s|\s+index)?|\s+inflation|\s+deflator)?"),
    ("pce", r"(?:headline\s+)?(?:pce|personal consumption expenditures?)\s+(?:price(?:s|\s+index)?|inflation|deflator)|(?:headline\s+)?pce\b"),
    ("x_corecore", r"core-core\s+(?:cpi|inflation)"),
    ("core_cpi", r"(?<![-\w])core\s+(?:cpi|consumer price(?:s|\s+index)|inflation)"),
    ("hicp", r"hicp|harmoni[sz]ed\s+(?:index\s+of\s+consumer\s+prices|inflation)"),
    ("cpi", r"cpi|consumer\s+price(?:s|\s+index)|inflation(?!\s+(?:expectations?|targets?|swaps?|outlook|risks?|fears?|pressures?|nowcast|breakevens?|gauge)|-adjusted|-linked|-protected|-indexed)"),
    ("gdp", r"gdp|gross\s+domestic\s+product|economic\s+growth|economy\s+(?:grew|expanded|contracted|shrank)"),
    ("growth", r"growth"),
    ("unemployment_change", r"unemployment\s+change|jobless\s+change"),
    ("jobless_claims", r"(?:initial\s+)?jobless\s+claims|initial\s+claims"),
    ("unemployment", r"(?:unemployment|jobless)\s+rate|unemployment(?!\s+(?:change|claims|benefits?|insurance))"),
    ("nfp", r"non-?farm\s+(?:payrolls?|employment(?:\s+change)?)|nfp|payrolls"),
    ("employment_change", r"employment\s+change"),
    ("wages", r"average\s+(?:hourly\s+|weekly\s+)?earnings|wage\s+growth"),
    ("pmi_manufacturing", r"manufacturing\s+pmi|pmi\s+manufacturing|ism\s+manufacturing(?:\s+pmi)?|manufacturing\s+purchasing\s+managers"),
    ("pmi_services", r"services\s+pmi|pmi\s+services|ism\s+services(?:\s+pmi)?|non-manufacturing\s+pmi|services\s+purchasing\s+managers"),
    ("pmi_composite", r"composite\s+pmi|pmi\s+composite"),
    ("retail_sales", r"retail\s+sales"),
    ("policy_move", r"(?:quarter|half)[-\s](?:percentage[-\s])?point\s+(?:increase|hike|rise|cut|reduction)|"
                    r"rate\s+(?:hike|cut|increase|rise|reduction)s?(?=\s+(?:to|at)\s+\d)|(?:hike|cut)(?=\s+to\s+\d)"),
    ("policy_rate", r"(?:policy|benchmark|key|interest|deposit(?:\s+facility)?|bank|cash|official\s+cash|overnight|refinancing|refi|repo|(?:federal|fed)\s+funds|funds)\s+rates?|rates"),
    # blockers
    ("x_other", r"inflation\s+(?:expectations?|targets?|swaps?|gauge)|inflation-(?:adjusted|linked|protected|indexed)|"
                r"trimmed\s+mean(?:\s+(?:cpi|inflation|measures?))?|weighted\s+median(?:\s+(?:cpi|inflation))?|"
                r"(?:underlying|trend|services|goods|food|energy|rent|wage|supercore|super-core|sticky)\s+(?:cpi|inflation|prices?)|"
                r"core\s+prices|insured\s+unemployment(?:\s+rate)?|youth\s+unemployment(?:\s+rate)?|gauge|producer\s+prices?|ppi|import\s+prices?|export\s+prices?|"
                r"trimmed\s+mean|weighted\s+median|continuing\s+claims|consumer\s+confidence|confidence|sentiment|"
                r"deflator|consumer\s+spending|personal\s+(?:income|spending)|spending|exports?|imports?|"
                r"capex|industrial\s+(?:production|profits?)|profits?|house\s+prices?|home\s+prices?|rents?|"
                r"wage\s+pressures?|services\s+inflation|goods\s+inflation|energy\s+prices?|food\s+prices?|"
                r"present\s+situation|expectations\s+index|yields?|stocks?|shares|futures|gold|silver|oil|crude|"
                r"bitcoin|dollar|euro\b|yen\b|sterling|pound|index\s+(?:fell|rose|closed)|s&p|nasdaq|dow|"
                r"nikkei|kospi|dax|ftse|cac|bonds?|treasur\w*|gilts?|bunds?|jgbs?|mortgage\s+rates?|mortgage|vacancy|"
                r"exchange\s+rates?|swap\s+rates?|real\s+rates?|market\s+rates?|neutral\s+rates?|terminal\s+rates?|"
                r"(?:earnings|profit|loan|credit|money(?:\s+supply)?|sales|revenue|eps|productivity|wage|job|payroll|employment|import|export)\s+growth|"
                r"participation|productivity|unit\s+labou?r\s+costs?|hours\s+worked"),
]
_MENTION_RE = re.compile("|".join(f"(?P<{k}>(?<![\\w-]){p}(?![\\w]))" for k, p in _MENTIONS), re.I)

# numbers: 3.2 · +0.3 · -0.2 · 1,105 · 119,000 · 4.25 — with an optional unit
_NUM_RE = re.compile(
    r"(?P<sign>[+\-−])?(?P<num>\d{1,3}(?:,\d{3})+(?!\d)|\d+(?:\.\d+)?)"
    r"(?P<unit>\s*(?:%|per\s?cent\b|percent\b|pct\b|bps?\b|basis\s+points?\b|ppts?\b|pp\b|[kK](?![a-zA-Z])|thousand\b))?"
)
_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%?\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*%")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September",
     "October", "November", "December"])}
_MONTH_ABBR = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "Jun": 6, "Jul": 7, "Aug": 8, "Sep": 9,
               "Sept": 9, "Oct": 10, "Nov": 11, "Dec": 12}
_MONTH_RE = re.compile(
    r"\b(?:(?P<full>January|February|March|April|June|July|August|September|October|November|December)"
    r"|(?P<may>May)(?=\s+(?:20\d\d|\d{1,2}\b))|(?<=in\s)(?P<may2>May)"
    r"|(?P<abbr>Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\.?(?=\s|$))"
    r"(?:\s+(?P<day>\d{1,2})(?!\d))?(?:,?\s+(?P<year>20\d\d))?")
_QUARTER_RE = re.compile(
    r"\b(?:Q(?P<q1>[1-4])(?!\s*FY)|(?P<q2>[1-4])Q(?:(?P<yy>\d\d)\b)?|(?P<qw>first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+quarter)"
    r"(?:\s*(?:of\s+)?(?P<year>20\d\d))?", re.I)
_MEETING_AFTER = re.compile(r"\w+\.?\s+(?:\w+\s+)?(?:rate\s+)?(?:hike|cut|meeting|decision|move|policy|"
                            r"BoJ|BOJ|Fed|ECB|BoE|FOMC|RBA|SNB|BoC|MPC)\b", re.I)
_NOT_PERIOD = re.compile(r"\b(?:since|than|until|till|before|after|from|last\s+seen|highest|lowest|fastest|slowest|"
                         r"strongest|weakest|most|least)\s+(?:in\s+|of\s+|the\s+)?(?:early\s+|late\s+|mid-?)?$", re.I)
_QWORD = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
_YEAR_RE = re.compile(r"\b(?:in|for|by|during|through|over|of|end[- ]|end\s+of|year[- ]end)\s*(?P<year>20[2-4]\d)\b", re.I)
_WEEK_RE = re.compile(r"week\s+ending\s+(?P<m>January|February|March|April|May|June|July|August|September|October|November|December)\s+(?P<d>\d{1,2})")

_YOY = re.compile(r"y/y|yoy|y-o-y|year[- ]on[- ]year|year[- ]over[- ]year|from\s+a\s+year\s+(?:earlier|ago)|"
                  r"a\s+year\s+earlier|annual\s+(?:rate|basis|pace|inflation)|annually|12-month|on\s+a\s+yearly\s+basis|yearly", re.I)
_MOM = re.compile(r"m/m|mom\b|m-o-m|month[- ]on[- ]month|month[- ]over[- ]month|(?:on|over|in)\s+the\s+month|monthly", re.I)
_QOQ = re.compile(r"q/q|qoq|q-o-q|quarter[- ]on[- ]quarter|quarter[- ]over[- ]quarter|on\s+the\s+quarter|quarterly", re.I)
_SAAR = re.compile(r"annuali[sz]ed|saar|annual\s+rate", re.I)

# kind cues
_AFTER_CONSENSUS = re.compile(r"^\s*(?:y/y|m/m|q/q|yoy|mom|qoq|annuali[sz]ed|saar)?\s*(?:\(\w+\))?\s*"
                              r"(?:expe\w*t\w*|exp\b|estimates?\b|est\b|consensus|forecast\b|f'cast|eyed|anticipated|"
                              r"projected|predicted)", re.I)
_AFTER_PREVIOUS = re.compile(r"^\s*(?:y/y|m/m|q/q|yoy|mom|qoq)?\s*(?:prelim\w*|previous(?:ly)?\b|prior\b|initially|first\s+estimate|advance)", re.I)
_BEFORE_PREVIOUS = re.compile(r"(?:prior(?:\s+was)?|prior\s+revised(?:\s+(?:up|down|higher|lower))?\s+to|previous(?:ly)?|prev\.?|"
                              r"from(?:\s+an?)?(?:\s+(?:upwardly|downwardly))?(?:\s+revised)?|"
                              r"revised(?:\s+(?:up|down|higher|lower))?\s+from|advance\s+estimate|"
                              r"(?:initial|first|advance|preliminary|prelim|flash)\s+(?:estimate|reading|print|figure)s?(?:\s+of)?|"
                              r"compared\s+(?:to|with)|following|after|last\s+month'?s?|preliminary\s+estimate)"
                              r"\s*(?:was|of|at)?\s*[:\-]?\s*$", re.I)
_BETWEEN_CONSENSUS = re.compile(r"expected|expectations?|consensus|forecasts?|forecasted|estimates?|poll\w*|anticipated|"
                                r"projected|predicted|economists\s+(?:see|expect)|seen\b|eyed", re.I)
_FUTURE = re.compile(r"\b(?:will|would|could|may|might|likely|set\s+to|poised|due|preview|ahead\s+of|to\s+be|"
                     r"should|expected|forecast|seen|projected|predicted|bets?|pricing|priced|odds|probability)\b", re.I)
_APPROX_BAD = re.compile(r"(?:just|slightly|well)?\s*(?:above|below|over|under|more\s+than|less\s+than|at\s+least|"
                         r"up\s+to|nearly|almost|near|north\s+of|south\s+of)\s*$", re.I)
_DECISION = re.compile(r"\b(?:held|holds|hold|holding|kept|keeps|keep|left|leaves|leave|maintain\w*|unchanged|"
                       r"raised|raises|raising|hiked|hikes|hiking|lifted|lifts|cut|cuts|cutting|lowered|lowers|"
                       r"reduced|reduces|voted|decided|delivered|increased|increases|set)\b", re.I)
_DELTA_BEFORE = re.compile(r"\b(?:down|up|by|increase\s+of|decrease\s+of|drop\s+of|rise\s+of|fall\s+of|"
                           r"gain\s+of|jump\s+of|change\s+of)\s*$", re.I)
_DECISION_DONE = re.compile(r"held|holds|kept|keeps|left|leaves|maintained|maintains|unchanged|raised|raises|hiked|hikes|"
                            r"lifted|lifts|cut|cuts|lowered|lowers|reduced|reduces|voted|decided|delivered|increased|"
                            r"increases|set", re.I)
_HIKE = re.compile(r"\b(?:hike[sd]?|hiking|rais\w+|increas\w+|lift\w*|tighten\w*)\b", re.I)
_CUT = re.compile(r"\b(?:cut|cuts|cutting|lower\w*|reduc\w+|eas\w+)\b", re.I)
_MARKET = re.compile(r"\b(?:markets?|traders?|investors?|futures|swaps?|pricing|priced|economists?|analysts?|"
                     r"poll\w*|consensus|street|surveyed)\b", re.I)
_WE = re.compile(r"\b(?:we|our)\s+(?:now\s+|still\s+|continue\s+to\s+|also\s+)?(?:expect|forecast|see|project|predict|"
                 r"anticipate|look\s+for|pencil|estimate|think|believe)", re.I)
_SEES = re.compile(r"\b(?:expects?|expecting|forecasts?|forecasting|sees|seeing|projects?|projecting|predicts?|"
                   r"anticipates?|estimates?|pencils?|looks?\s+for|calls?\s+for)\b", re.I)
_BAD_AFTER = re.compile(r"^\s*(?:target|targets|threshold|hurdle|of\s+GDP|of\s+gdp|of\s+the\s+(?:workforce|economy)|"
                        r"probability|chance|odds|years?\b(?![-\s]+(?:on|over)[-\s]+year)|-year|months?\b(?![-\s]+(?:on|over)[-\s]+month)|-month|weeks?\b|bn\b|billion|million|trillion|"
                        r"mn\b|tn\b)", re.I)

# institutions we may name as forecasters (catalog + desks quoted in news)
_EXTRA_INSTITUTIONS = [
    "Goldman Sachs", "Goldman", "JPMorgan", "J.P. Morgan", "JP Morgan", "Morgan Stanley", "UBS", "Barclays",
    "BofA", "Bank of America", "Citi", "Citigroup", "Commerzbank", "ING", "Societe Generale", "SocGen",
    "BNP Paribas", "Deutsche Bank", "HSBC", "Nomura", "MUFG", "Mizuho", "Wells Fargo", "Scotiabank",
    "TD Securities", "RBC", "CIBC", "BMO", "Danske Bank", "Nordea", "SEB", "DBS", "OCBC", "UOB",
    "Rabobank", "ABN AMRO", "Natixis", "Credit Agricole", "UniCredit", "Intesa Sanpaolo", "Westpac",
    "ANZ", "NAB", "Macquarie", "Capital Economics", "Oxford Economics", "Pantheon Macroeconomics",
    "Pantheon", "IMF", "OECD", "European Commission", "World Bank", "BlackRock", "PIMCO", "Amundi",
    "Standard Chartered", "Lloyds", "NatWest", "Jefferies", "Bloomberg Economics", "Danske", "KBC",
    "Berenberg", "Julius Baer", "Fed", "ECB", "BoE", "BoJ", "BOJ", "RBA", "BoC", "SNB", "RBNZ", "Saxo", "Crédit Agricole", "Société Générale", "Schroders", "Vanguard",
]


def _institution_names() -> list[str]:
    names = set(_EXTRA_INSTITUTIONS)
    for meta in catalog.META.values():
        if meta.get("category") in ("commercial_bank", "fund", "multilateral", "central_bank"):
            names.add(meta.get("institution", ""))
    return sorted((n for n in names if n and len(n) > 1), key=len, reverse=True)


_INST_RE = re.compile(r"(?<![\w])(" + "|".join(re.escape(n) for n in _institution_names()) + r")(?![\w])")
_TITLE_ATTRIB_RE = re.compile(r"\s[–—-]\s+([A-Z][\w&.'’ ]{1,40})\s*$")
_FORECASTER_CATS = ("commercial_bank", "fund", "multilateral", "central_bank")


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------
def clean_text(s: Optional[str]) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("\xa0", " ").replace("\r", "")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


_SPLIT_RE = re.compile(r"\n+|(?<=[a-z]{2})(?=[A-Z]{2})|(?<=[.!?])\s+(?=[A-Z\"“(])|(?<=[a-z%)][.!?])(?=[A-Z][a-z])|(?<=[a-z%\d)])(?=[A-Z][a-z])|(?<=:)(?=[A-Z])|;\s*")


def segments(text: str) -> list[tuple[int, int]]:
    """Clause spans of `text`: sentences, lines, and the run-on lines of
    wire headlines ("…vs 4k expectedPrior 4kGermany…")."""
    spans, start = [], 0
    for m in _SPLIT_RE.finditer(text):
        if m.start() > start:
            spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def _parse_number(tok: str) -> float:
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", tok):
        return float(tok.replace(",", ""))
    return float(tok.replace(",", "."))


def number_tokens(quote: str) -> list[float]:
    """Every number written in `quote`, with its sign when the sign is a sign
    (`-0.2%`, `(−1.1%)`) and not a dash (`3.75%-4%`)."""
    out = []
    for m in _NUM_RE.finditer(quote):
        v = _parse_number(m.group("num"))
        if m.group("sign"):
            i = m.start()
            prev = quote[i - 1] if i > 0 else " "
            if prev in " (\t\n:/[" or i == 0:
                v = -v if m.group("sign") in "-−" else v
        out.append(v)
    return out


def quote_contains_value(quote: str, value: float) -> bool:
    """Golden rule: `value` is written, as such, in `quote`."""
    if quote is None or value is None:
        return False
    return any(abs(v - value) < 1e-9 for v in number_tokens(quote))


def _fmt_value(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(v)


def make_quote(text: str, seg: tuple[int, int], num_span: tuple[int, int], limit: int = 200) -> str:
    """Exact excerpt of `text` (<= limit chars) around the number, inside its clause."""
    a, b = seg
    s = text[a:b]
    if len(s.strip()) <= limit:
        return s.strip()
    ns, ne = num_span[0] - a, num_span[1] - a
    lo = max(0, ns - 110)
    hi = min(len(s), lo + limit)
    lo = max(0, min(lo, hi - limit))
    if lo > 0:                                   # cut on a word boundary
        sp = s.find(" ", lo, ns)
        lo = sp + 1 if sp != -1 else lo
    if hi < len(s):
        sp = s.rfind(" ", ne, hi)
        hi = sp if sp != -1 else hi
    return s[lo:hi].strip()


# ---------------------------------------------------------------------------
# periods
# ---------------------------------------------------------------------------
def _month_period(month: int, year: Optional[int], pub: date, kind: str) -> str:
    if year is None:
        if kind == "forecast":
            year = pub.year if month >= pub.month - 1 else pub.year + 1
        elif kind == "consensus":
            year = pub.year if month <= pub.month + 1 else pub.year - 1
            if pub.month == 12 and month == 1:
                year = pub.year + 1
        else:
            year = pub.year if month <= pub.month else pub.year - 1
    return f"{year:04d}-{month:02d}"


def _quarter_period(q: int, year: Optional[int], pub: date, kind: str) -> str:
    if year is None:
        cur = (pub.month - 1) // 3 + 1
        if kind == "forecast":
            year = pub.year if q >= cur else pub.year + 1
        else:
            year = pub.year if q <= cur else pub.year - 1
    return f"{year:04d}-Q{q}"


def _periods_in(s: str, pub: date, kind: str) -> list[tuple[int, str]]:
    """[(position, period)] found in `s`."""
    out = []
    for m in _WEEK_RE.finditer(s):
        mo = _MONTHS[m.group("m")]
        y = pub.year if mo <= pub.month else pub.year - 1
        try:
            out.append((m.start(), date(y, mo, int(m.group("d"))).isoformat()))
        except ValueError:
            pass
    for m in _MONTH_RE.finditer(s):
        if _NOT_PERIOD.search(s[max(0, m.start() - 25):m.start()]):
            continue
        if any(m.start() >= p and m.start() < p + 40 for p, per in out if len(per) == 10):
            continue
        name = m.group("full") or m.group("may") or m.group("may2") or m.group("abbr")
        mo = _MONTHS.get(name) or _MONTH_ABBR.get(name) or 5
        y = int(m.group("year")) if m.group("year") else None
        out.append((m.start(), _month_period(mo, y, pub, kind)))
    for m in _QUARTER_RE.finditer(s):
        if _NOT_PERIOD.search(s[max(0, m.start() - 25):m.start()]):
            continue
        q = int(m.group("q1") or m.group("q2") or _QWORD[m.group("qw").lower()])
        y = int(m.group("year")) if m.group("year") else (2000 + int(m.group("yy")) if m.group("yy") else None)
        out.append((m.start(), _quarter_period(q, y, pub, kind)))
    for m in _YEAR_RE.finditer(s):
        if not any(abs(p - m.start()) < 12 for p, _ in out):
            out.append((m.start("year"), m.group("year")))
    return sorted(out)


def period_end(period: str) -> Optional[str]:
    """Last day of a period: the horizon of a forecast."""
    if not period:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        return period
    if re.fullmatch(r"\d{4}", period):
        return f"{period}-12-31"
    m = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        return (date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1) - timedelta(days=1)).isoformat()
    m = re.fullmatch(r"(\d{4})-(\d{2})", period)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return (date(y + (mo == 12), 1 if mo == 12 else mo + 1, 1) - timedelta(days=1)).isoformat()
    return None


_PERIOD_OK = re.compile(r"^(?:\d{4}|\d{4}-(?:0[1-9]|1[0-2])|\d{4}-Q[1-4]|\d{4}-\d{2}-\d{2})?$")


# ---------------------------------------------------------------------------
# fact record + validation (shared by both passes)
# ---------------------------------------------------------------------------
class Fact(BaseModel):
    zone: Literal[ZONES]  # type: ignore[valid-type]
    geo: str
    indicator: Literal[tuple(INDICATORS)]  # type: ignore[valid-type]
    kind: Literal[KINDS]  # type: ignore[valid-type]
    value: float
    unit: Literal[UNITS]  # type: ignore[valid-type]
    period: str = ""
    horizon: Optional[str] = None
    quote: str = Field(min_length=3, max_length=200)
    institution: Optional[str] = None
    method: Literal["regex", "llm"] = "regex"
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("period")
    @classmethod
    def _period(cls, v):
        v = v or ""
        if not _PERIOD_OK.match(v):
            raise ValueError(f"bad period {v!r}")
        return v

    @field_validator("geo")
    @classmethod
    def _geo(cls, v):
        if v not in GEO_ZONE:
            raise ValueError(f"unknown geo {v!r}")
        return v


def accept(fact: Fact, source_text: str) -> bool:
    """Both passes end here: the quote is an exact excerpt of the article, it
    contains the value, and geo belongs to zone."""
    return (fact.quote in source_text and quote_contains_value(fact.quote, fact.value)
            and GEO_ZONE.get(fact.geo) == fact.zone)


def dedupe(facts: list[Fact]) -> list[Fact]:
    """One fact per (zone, geo, indicator, kind, period) within a paper: the
    most confident, then the first stated."""
    best: dict[tuple, Fact] = {}
    for f in facts:
        k = (f.zone, f.geo, f.indicator, f.kind, f.period)
        if k not in best or f.confidence > best[k].confidence:
            best[k] = f
    return list(best.values())


# ---------------------------------------------------------------------------
# pass 1: rules
# ---------------------------------------------------------------------------
def _paper_text(paper) -> tuple[str, str]:
    title = clean_text(getattr(paper, "title", "") or "")
    body = clean_text(getattr(paper, "abstract", "") or "")
    ft = clean_text((getattr(paper, "full_text", "") or "")[:20000])
    if ft and body and body[:80] in ft:
        body = ft
    elif ft:
        body = f"{body}\n{ft}" if body else ft
    return title, f"{title}\n{body}" if body else title


def _pub_date(paper) -> date:
    d = getattr(paper, "published_date", None) or getattr(paper, "created_at", None) or utcnow()
    return d.date() if isinstance(d, datetime) else d


def _source_meta(paper) -> dict:
    return catalog.META.get(getattr(paper, "source", "") or "", {})


def _forecaster(seg_text: str, title: str, paper) -> Optional[str]:
    """Institution that owns a forecast stated in `seg_text`, or None."""
    for m in _INST_RE.finditer(seg_text):
        tail = seg_text[m.end():m.end() + 60]
        if _SEES.search(tail) or re.match(r"\s*(?:’s|'s)\s+[\w\s.\-]{0,20}forecast", tail):
            return m.group(1)
    meta = _source_meta(paper)
    attrib = _TITLE_ATTRIB_RE.search(title or "")
    if attrib:
        mi = _INST_RE.search(attrib.group(1))
        if mi:
            return mi.group(1)
    if meta.get("category") in _FORECASTER_CATS and _WE.search(seg_text):
        return meta.get("institution")
    return None


def _resolve_geo(hits_before, title_geo, implied, src_geo) -> tuple[Optional[str], float]:
    """(geo, confidence penalty) - nearest place word before the number in the
    clause, then the title, then what the indicator implies, then the source."""
    if hits_before:
        g = hits_before[-1][2]
        return g, 0.0
    if title_geo is not False:
        return title_geo, 0.1
    if implied:
        return implied, 0.05
    if src_geo:
        return src_geo, 0.15
    return None, 0.0


def _title_geo(title: str):
    """First place word of the title (False when there is none)."""
    hits = _geo_hits(title)
    return hits[0][2] if hits else False


_IMPLIED_GEO = {"core_pce": "US", "pce": "US", "nfp": "US", "jobless_claims": "US"}


def _qualifier(s: str, ns: int, ne: int) -> Optional[str]:
    """yoy | mom | qoq | saar for the number at s[ns:ne]: written right after it
    (<= 16 chars), else the nearest one before it (from the mention on)."""
    after, before = None, None
    for name, rx in (("saar", _SAAR), ("yoy", _YOY), ("mom", _MOM), ("qoq", _QOQ)):
        for m in rx.finditer(s):
            if ne <= m.start() <= ne + 16 and (after is None or m.start() < after[0]):
                after = (m.start(), name)
            elif m.end() <= ns and (before is None or m.end() > before[0]):
                before = (m.end(), name)
    return (after or before or (None, None))[1]


def _indicator_for(family: str, qual: Optional[str], geo: str, value: float, unit: str,
                   period_hint: str, kind: str, generic_pce: bool) -> Optional[str]:
    if family in ("cpi", "core_cpi") and generic_pce:
        family = "pce" if family == "cpi" else "core_pce"
    if family == "cpi":
        if qual == "mom":
            return "cpi_mom"
        if qual in ("yoy", None, "saar") and (qual == "yoy" or abs(value) >= 1.0):
            return "cpi_yoy"
        return None
    if family == "core_cpi":
        if qual == "mom":
            return "core_cpi_mom"
        if qual == "yoy" or (qual is None and abs(value) >= 1.0):
            return "core_cpi_yoy"
        return None
    if family == "hicp":
        if qual == "mom":
            return None
        if qual == "yoy" or abs(value) >= 1.0:
            return "cpi_yoy" if geo == "EA" else "hicp_yoy"
        return None
    if family == "pce":
        return "pce_yoy" if (qual == "yoy" or (qual is None and abs(value) >= 1.0)) else None
    if family == "core_pce":
        if qual == "mom":
            return "core_pce_mom"
        return "core_pce_yoy" if (qual == "yoy" or (qual is None and abs(value) >= 1.0)) else None
    if family in ("gdp", "growth"):
        if unit != "%":
            return None
        if qual == "saar":
            return "gdp_qoq_saar"
        if qual == "yoy":
            return "gdp_yoy" if "Q" in period_hint or not re.fullmatch(r"\d{4}", period_hint or "") else "gdp_annual"
        if qual == "qoq":
            return "gdp_qoq"
        if "Q" in (period_hint or ""):
            return "gdp_qoq_saar" if geo == "US" else "gdp_qoq"
        if re.fullmatch(r"\d{4}", period_hint or "") and kind in ("forecast", "consensus"):
            return "gdp_annual"
        return None
    if family == "unemployment":
        return "unemployment" if unit == "%" and 0 < value < 40 else None
    if family == "unemployment_change":
        return "unemployment_change" if unit in ("k", "jobs") else None
    if family == "jobless_claims":
        return "jobless_claims" if unit in ("k", "jobs") else None
    if family == "nfp":
        return "nfp" if unit in ("k", "jobs") else None
    if family == "employment_change":
        return "employment_change" if unit in ("k", "jobs") else None
    if family == "wages":
        return "wages_yoy" if unit == "%" and qual == "yoy" else None
    if family.startswith("pmi_"):
        return family if unit == "index" and 20 <= value <= 80 else None
    if family == "retail_sales":
        if unit != "%":
            return None
        return {"mom": "retail_sales_mom", "yoy": "retail_sales_yoy"}.get(qual or "")
    return None


def _unit_of(m: re.Match, seg: str) -> Optional[str]:
    u = (m.group("unit") or "").strip().lower()
    if u in ("%", "percent", "per cent", "percent", "pct") or u.startswith("per"):
        return "%"
    if u.startswith("bp") or u.startswith("basis"):
        return "bp"
    if u in ("ppt", "ppts", "pp"):
        return None                                # percentage-point changes: not a level
    if u in ("k", "thousand"):
        return "k"
    if "," in m.group("num") and _parse_number(m.group("num")) >= 1000:
        return "jobs"
    return "index" if not u else None


def _num_value(m: re.Match, text: str) -> float:
    v = _parse_number(m.group("num"))
    sign = m.group("sign")
    if sign:
        i = m.start()
        prev = text[i - 1] if i > 0 else " "
        if prev in " (\t\n:/[" or i == 0:
            v = -v if sign in "-−" else v
    return v


_DECLINE_BEFORE = re.compile(r"\b(?:drops?|dropped|falls?|fell|declines?|declined|contracts?|contracted|shrank|shrinks|"
                             r"slips?|slipped|slides?|slid|dips?|dipped|down|decreased?|plunged?|tumbled?|sank|sinks)\s*(?:by\s+)?$", re.I)
_CONS_SOFT = re.compile(r"(?:than|above|below|beat\w*|miss\w*|exceed\w*|in\s+line\s+with|undershoot\w*|overshoot\w*|"
                        r"top\w*|surpass\w*|hotter|cooler|softer|stronger|weaker|higher|lower|faster|slower)[\s-]+(?:the\s+|market\s+|economists['’]?\s+)?$", re.I)
_PER_PREP = re.compile(r"(?:\b(?:in|for|during|of|over)\s+(?:the\s+)?|^\s*(?:y/y|m/m|q/q|yoy|mom|qoq|,|\(|\s)*)$", re.I)
_SHARE = re.compile(r"contribut\w*|weight|share\s+of|basket|accounted|percentage\s+points?", re.I)


def _cons_cue_before(s: str, lo: int, hi: int) -> bool:
    """An expectation word in s[lo:hi] that is not a comparison ("hotter than
    expected", "above forecasts")."""
    for m in _BETWEEN_CONSENSUS.finditer(s, lo, hi):
        if not _CONS_SOFT.search(s[max(0, m.start() - 30):m.start()]):
            return True
    return False


def _pick_mention(mentions, ns: int, s: str):
    """Nearest indicator mention before position `ns` (an acronym in brackets
    right after a mention belongs to it; a generic 'inflation' after a PCE
    mention is that PCE)."""
    before = [x for x in mentions if x[1] <= ns and ns - x[1] <= 140]
    if not before:
        return None
    while len(before) >= 2 and s[before[-2][1]:before[-1][0]].strip() in ("(", "", "-", "–"):
        if before[-1][2].startswith("x_"):
            break
        before.pop()
    ms, me, fam = before[-1]
    if fam in ("cpi", "core_cpi") and s[ms:me].lower().endswith("inflation") and ns - me > 60:
        return None
    if fam == "cpi" and s[ms:me].lower().startswith("inflation"):
        for x in reversed(before[:-1]):
            if x[2] in ("pce", "core_pce", "core_cpi", "hicp", "cpi"):
                fam = x[2] if x[2] != "cpi" else fam
                break
            if x[2].startswith("x_") or x[2] not in ("cpi",):
                break
    return ms, me, fam


def extract_rules(paper) -> list[Fact]:
    """Pass 1: rule-based facts of one paper (already golden-rule checked)."""
    title, text = _paper_text(paper)
    if not re.search(r"\d", text):
        return []
    pub = _pub_date(paper)
    meta = _source_meta(paper)
    src_zone = meta.get("zone")
    src_geo = ZONE_AGGREGATE.get(src_zone) if meta.get("category") in ("central_bank", "multilateral") else None
    tgeo = _title_geo(title)
    lower = text.lower()
    generic_pce = (("pce" in lower or "personal consumption expenditure" in lower)
                   and "cpi" not in lower and "consumer price" not in lower)
    preview_title = bool(re.search(r"\bpreview\b|\bahead of\b|\bwhat to expect\b|\bweek ahead\b", title, re.I))
    title_period = {k: [p for pos, p in _periods_in(title, pub, k) if not _MEETING_AFTER.match(title[pos:])]
                    for k in ("actual", "consensus")}
    facts: list[Fact] = []
    carry = None                                    # (family, geo) of the previous clause

    def skip():
        return None

    for seg in segments(text):
        a, b = seg
        s = text[a:b]
        is_title = a == 0
        mentions = [(m.start(), m.end(), m.lastgroup) for m in _MENTION_RE.finditer(s)]
        geo_hits = [h for h in _geo_hits(s) if not re.match(r"\s+(?:Eastern|Pacific|Central|time\b|ET\b)", s[h[1]:h[1] + 9])]
        cb_hits = _geo_hits(s, central_banks=True)
        nums = list(_NUM_RE.finditer(s))
        seg_has_ind = any(not k.startswith("x_") for _, _, k in mentions)
        if not nums:
            if mentions:
                carry = None
            continue
        carried = False
        if not seg_has_ind and carry and re.match(r"\s*\(?\s*(?:prior|previous|revised|expected|consensus|est)", s, re.I):
            mentions = [(0, 0, carry[0])] + mentions
            carried = True
        forecaster = _forecaster(s, title, paper) if (_SEES.search(s) or _WE.search(s) or "forecast" in s.lower()) else None
        prev_end = 0
        last_family = None
        last_unit = None
        seg_actual_period = None
        seg_actual = False
        for m in nums:
            ns, ne = m.start(), m.end()
            pe, prev_end = prev_end, ne
            if s[max(0, ns - 1):ns] in ("$", "€", "£", "¥") or re.match(r"(?:st|nd|rd|th)\b", s[ne:ne + 2]):
                continue
            if s[max(0, ns - 2):ns] in ("A$", "C$", "NZ", "S$", "HK"):
                continue
            if re.fullmatch(r"20\d\d", m.group("num")) and not m.group("unit"):
                continue
            if _BAD_AFTER.match(s[ne:ne + 20]) or re.match(r"\s*[-–]\s*(?:year|month|week|quarter|day)", s[ne:ne + 12]):
                continue
            unit = _unit_of(m, s)
            if unit == "index" and last_unit and ns - last_unit[0] <= 30 and re.search(
                    r"\b(?:vs\.?|versus|expected|from|to|prior|previous)\b", s[last_unit[0]:ns], re.I):
                unit = last_unit[1]                 # "4.3% vs 3.5 estimate": same unit as the print
            if unit is None:
                continue
            if unit != "index":
                last_unit = (ne, unit)
            pick = _pick_mention(mentions, ns, s)
            if not pick:
                continue
            ms, me, fam = pick
            if fam.startswith("x_"):
                continue
            if fam == "growth" and not re.search(r"econom|gdp", s + " " + title, re.I):
                continue
            value = _num_value(m, s)
            between = s[max(me, pe):ns]
            after = s[ne:ne + 40]
            if _APPROX_BAD.search(between[-25:]) or _SHARE.search(between) or _SHARE.match(s[ne:ne + 25].strip()):
                continue
            # ---- kind --------------------------------------------------------
            conf = 0.75
            kind = None
            if _AFTER_PREVIOUS.match(after):
                kind, conf = "previous", 0.85
            elif _AFTER_CONSENSUS.match(after):
                kind, conf = "consensus", 0.85
                if re.match(r"\s*(?:forecast)", after, re.I) and forecaster:
                    kind = "forecast"
            elif _BEFORE_PREVIOUS.search(s[max(pe, ns - 45):ns]):
                kind, conf = "previous", 0.8
            elif _cons_cue_before(s, pe if seg_actual else 0, ns):
                if forecaster and not _MARKET.search(s[:ns]):
                    kind, conf = "forecast", 0.7
                else:
                    kind, conf = "consensus", 0.65
            elif forecaster and _SEES.search(s[:ns]):
                kind, conf = "forecast", 0.65
            elif (preview_title and is_title) or _FUTURE.search(between) or re.search(r"\bpreview\b", s, re.I):
                kind = None
            else:
                kind = "actual"
            level = fam in ("unemployment", "jobless_claims") or fam.startswith("pmi_") or (
                fam in ("policy_rate", "policy_move") and unit == "%")
            if level and _DELTA_BEFORE.search(s[max(pe, ns - 20):ns]):
                continue                            # "down 13,000": a change, not the level
            if not level and fam not in ("policy_rate", "policy_move") and kind == "actual" and value > 0 and not m.group("sign") \
                    and _DECLINE_BEFORE.search(s[max(pe, ns - 25):ns]):
                continue                            # "GDP drops 0.2%": the sign is not written
            # ---- policy rate ----------------------------------------------------
            if fam in ("policy_rate", "policy_move"):
                cb = [h for h in cb_hits if h[0] < ns] or cb_hits
                if not cb and title:
                    cb = _geo_hits(title, central_banks=True)
                if not cb:
                    cb = _geo_hits(text[:a], central_banks=True)[-1:]
                if not cb and meta.get("category") == "central_bank" and src_zone in ZONE_AGGREGATE:
                    cb = [(0, 0, ZONE_AGGREGATE[src_zone])]
                if not cb or cb[-1][2] is None:
                    continue
                geo = cb[-1][2]
                if unit == "bp":
                    ind = "rate_hike_bp" if _HIKE.search(s) else ("rate_cut_bp" if _CUT.search(s) else None)
                    if ind is None or not (5 <= value <= 200):
                        continue
                elif unit == "%":
                    rng = None
                    for r in _RANGE_RE.finditer(s):
                        if r.start(1) <= ns < r.end(2):
                            rng = r
                    if rng and rng.start(1) <= ns < rng.end(1):    # lower bound: keep the upper one
                        continue
                    ind = "policy_rate"
                    if not (-1.0 <= value <= 25.0):
                        continue
                else:
                    continue
                dec = None
                for d in _DECISION.finditer(s, 0, ns):
                    if _DECISION_DONE.fullmatch(d.group(0)) and not re.search(
                            r"\b(?:to|will|would|could|may|might|should|not)\s+$", s[max(0, d.start() - 8):d.start()]):
                        dec = d
                if kind == "previous":
                    pass
                elif dec and not _FUTURE.search(s[dec.start():ns]):
                    kind, conf = "decision", 0.8
                elif fam == "policy_move" and not _FUTURE.search(s) and not _SEES.search(s) and kind == "actual":
                    kind, conf = "decision", 0.7
                elif forecaster and (_FUTURE.search(s) or _SEES.search(s)):
                    kind, conf = "forecast", 0.65
                elif (_FUTURE.search(s) or _SEES.search(s)) and (kind == "consensus" or re.search(
                        r"expected|consensus|pric|markets?\b|economists?|poll|analysts?|traders?", s, re.I)):
                    kind, conf = "consensus", 0.6
                elif _FUTURE.search(s) or _SEES.search(s):
                    continue
                elif re.search(r"\b(?:currently|stands?\s+at|now\s+at|current)\b", s[:ns], re.I):
                    kind, conf = "actual", 0.6
                else:
                    continue
                family_key = "policy_rate"
                gpen = 0.0
            else:
                if kind is None:
                    continue
                hits_before = [h for h in geo_hits if h[1] <= ns]
                geo, gpen = _resolve_geo(hits_before, tgeo, _IMPLIED_GEO.get(fam), src_geo)
                if not hits_before and carried and ms == 0 and me == 0:
                    geo, gpen = carry[1], 0.05
                if geo is None:
                    continue
                family_key = fam
                ind = None
            # ---- period ----------------------------------------------------------
            per_list = _periods_in(s, pub, kind)
            after_p = [p for pos, p in per_list
                       if ne <= pos <= ne + 30 and _PER_PREP.search(s[ne:pos]) and not re.search(r"\d", s[ne:pos])]
            before_p = [p for pos, p in per_list if pos < ns and pos >= max(0, ms - 60)]
            if kind == "previous":
                period = after_p[0] if after_p else ""
            elif family_key == "policy_rate":
                if kind == "actual":
                    period = pub.isoformat()[:7]
                else:
                    near = [p for pos, p in per_list if abs(pos - ns) <= 80] or [p for _, p in per_list]
                    period = (after_p[0] if after_p else (near[-1] if near else ""))
                    if kind == "decision" and not period:
                        period = pub.isoformat()[:7]
            else:
                if kind == "consensus" and not after_p and seg_actual_period:
                    period = seg_actual_period
                else:
                    period = after_p[0] if after_p else (before_p[-1] if before_p else "")
                if not period and kind in ("actual", "consensus"):
                    tp = title_period[kind]
                    period = tp[0] if tp else ""
            if family_key != "policy_rate":
                qual = _qualifier(s[ms:min(len(s), ne + 20)], ns - ms, ne - ms)
                if family_key in ("cpi", "core_cpi", "hicp", "pce", "core_pce") and "Q" in period:
                    continue                        # quarterly inflation measures are not in the list
                ind = _indicator_for(family_key, qual, geo, value, unit, period, kind, generic_pce)
                if ind is None:
                    continue
                if qual is None and family_key in ("cpi", "core_cpi", "hicp", "pce", "core_pce"):
                    conf -= 0.1
            exp_unit = INDICATORS[ind][1]
            if exp_unit == "k" and unit not in ("k", "jobs"):
                continue
            if exp_unit in ("%", "bp", "index") and unit != exp_unit:
                continue
            institution = forecaster if kind == "forecast" else None
            if kind == "forecast" and not institution:
                continue
            horizon = period_end(period) if kind == "forecast" else None
            quote = make_quote(text, seg, (a + ns, a + ne))
            try:
                f = Fact(zone=GEO_ZONE[geo], geo=geo, indicator=ind, kind=kind, value=value, unit=unit,
                         period=period, horizon=horizon, quote=quote, institution=institution,
                         method="regex", confidence=round(max(0.3, conf - gpen), 2))
            except ValidationError:
                continue
            if accept(f, text):
                facts.append(f)
                last_family = (family_key, geo)
                if kind == "actual":
                    seg_actual = True
                    if period and seg_actual_period is None:
                        seg_actual_period = period
        if seg_has_ind:
            carry = last_family or carry
        elif mentions and not carried:
            carry = None
    return dedupe(facts)


# ---------------------------------------------------------------------------
# pass 2: local LLM (strict JSON, schema validated)
# ---------------------------------------------------------------------------
LLM_MIN_IMPACT = float(os.environ.get("MF_LLM_MIN_IMPACT", "60"))
_LLM_HINT = re.compile(r"\b(?:cpi|inflation|gdp|unemployment|jobless|payrolls|pmi|retail sales|"
                       r"interest rate|policy rate|deposit rate|bank rate|cash rate|funds rate|hicp|pce)\b", re.I)


class _LLMFact(BaseModel):
    zone: str
    geo: Optional[str] = None
    indicator: str
    kind: str
    value: float
    unit: str
    period: Optional[str] = ""
    institution: Optional[str] = None
    quote: str


class _LLMOut(BaseModel):
    facts: list[_LLMFact] = Field(default_factory=list)


LLM_SCHEMA = {  # the shape validate_llm_output enforces (documentation; LFM-3 cannot take it as `format`)
    "type": "object",
    "properties": {"facts": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "zone": {"type": "string", "enum": list(ZONES)},
            "geo": {"type": "string", "enum": list(GEO_ZONE)},
            "indicator": {"type": "string", "enum": list(INDICATORS)},
            "kind": {"type": "string", "enum": list(KINDS)},
            "value": {"type": "number"},
            "unit": {"type": "string", "enum": list(UNITS)},
            "period": {"type": "string"},
            "institution": {"type": ["string", "null"]},
            "quote": {"type": "string"},
        },
        "required": ["zone", "geo", "indicator", "kind", "value", "unit", "period", "quote"],
    }}},
    "required": ["facts"],
}


def llm_prompt(text: str, pub: date) -> list[dict]:
    """LFM-3 has no structured-output mode in Ollama ("structured output is
    unavailable"): the shape is given by an example, then validated here."""
    sys_msg = (
        "You copy macroeconomic figures out of a news text. Reply with JSON only, shaped exactly like:\n"
        '{"facts": [{"zone": "GBP", "geo": "UK", "indicator": "cpi_yoy", "kind": "actual", "value": 3.6, '
        '"unit": "%", "period": "2026-10", "institution": null, "quote": "UK inflation rose to 3.6% in October"}]}\n'
        "Rules:\n"
        "- Only figures written in the text. Never add a figure that is not in the text. If there is none, "
        'reply {"facts": []}.\n'
        '- "quote" is copied character for character from the text (at most 200 characters) and contains the figure.\n'
        f"- indicator is one of: {', '.join(INDICATORS)}. Anything else (stocks, yields, oil, FX) is skipped.\n"
        "- kind: actual = published figure; consensus = what markets or economists expected; previous = prior "
        "figure; forecast = a named institution's projection (put it in \"institution\"); decision = a rate "
        "decision a central bank has taken.\n"
        f"- unit: {', '.join(UNITS)}. zone: {', '.join(ZONE_AGGREGATE)}. geo: {', '.join(GEO_ZONE)}.\n"
        "- period: YYYY-MM, YYYY-Qn or YYYY.")
    return [{"role": "system", "content": sys_msg},
            {"role": "user", "content": f"Text (published {pub.isoformat()}):\n{text[:3500]}"}]


def _json_payload(raw: str):
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except ValueError:
        i, j = raw.find("{"), raw.rfind("}")
        if i == -1 or j <= i:
            raise
        return json.loads(raw[i:j + 1])


def kind_consistent(kind: str, quote: str) -> bool:
    """The words of the quote must allow the kind the model chose (a model
    calling a bank's expectation a 'decision' is caught here)."""
    future = bool(_FUTURE.search(quote) or _SEES.search(quote) or re.search(r"\bexpect\w*", quote, re.I))
    if kind == "decision":
        return bool(_DECISION.search(quote)) and not future
    if kind == "forecast":
        return bool(_SEES.search(quote) or _WE.search(quote) or re.search(r"forecast|expect|project|see", quote, re.I))
    if kind == "consensus":
        return bool(_BETWEEN_CONSENSUS.search(quote) or re.search(r"expect|pric|consensus|poll", quote, re.I))
    if kind == "previous":
        return bool(re.search(r"prior|previous|from|revised|last|earlier|prelim", quote, re.I))
    return not future or bool(re.search(r"\bvs\.?\b|versus|than", quote, re.I))


def validate_llm_output(raw: str | dict, source_text: str, paper=None) -> list[Fact]:
    """Strict validation of the LLM answer. Malformed JSON or a wrong schema
    yields nothing; each fact then passes the same checks as the rules."""
    try:
        payload = raw if isinstance(raw, dict) else _json_payload(raw)
        out = _LLMOut.model_validate(payload)
    except (ValueError, ValidationError, TypeError):
        return []
    pub = _pub_date(paper) if paper is not None else utcnow().date()
    facts = []
    for x in out.facts:
        geo = x.geo or ZONE_AGGREGATE.get(x.zone)
        kind = x.kind
        institution = (x.institution or "").strip() or None
        if kind == "forecast" and not institution:
            continue
        period = (x.period or "").strip()
        try:
            f = Fact(zone=x.zone, geo=geo or "", indicator=x.indicator, kind=kind, value=x.value,
                     unit=x.unit, period=period, horizon=period_end(period) if kind == "forecast" else None,
                     quote=x.quote.strip(), institution=institution if kind == "forecast" else None,
                     method="llm", confidence=0.5)
        except ValidationError:
            continue
        if INDICATORS[f.indicator][1] != f.unit and not (INDICATORS[f.indicator][1] == "k" and f.unit == "jobs"):
            continue
        if f.period and re.fullmatch(r"\d{4}", f.period) and abs(int(f.period) - pub.year) > 3:
            continue
        if accept(f, source_text) and kind_consistent(f.kind, f.quote):
            facts.append(f)
    return dedupe(facts)


def _ollama_settings() -> tuple[str, str]:
    url = os.environ.get("MF_OLLAMA_URL")
    model = os.environ.get("MF_LLM_MODEL")
    if not (url and model):
        try:
            from embedding_prop import Settings  # same LFM-3 as the analysis layer
            st = Settings()
            url, model = url or st.ollama_base_url, model or st.llm_model
        except Exception:
            url, model = url or "http://127.0.0.1:11434", model or "lfm3:latest"
    return url.rstrip("/"), model


def extract_llm(paper, *, client=None) -> list[Fact]:
    """Pass 2 for one paper. Network errors yield []."""
    import httpx
    _, text = _paper_text(paper)
    url, model = _ollama_settings()
    payload = {"model": model, "messages": llm_prompt(text, _pub_date(paper)), "stream": False,
               "think": False, "options": {"temperature": 0.0, "num_ctx": 4096, "num_predict": 700}}
    try:
        c = client or httpx.Client(timeout=90.0)
        r = c.post(f"{url}/api/chat", json=payload)
        r.raise_for_status()
        content = r.json()["message"]["content"]
    except Exception as e:
        print(f"    [!] macro-facts LLM failed for {getattr(paper, 'id', '?')}: {type(e).__name__}", flush=True)
        return []
    return validate_llm_output(content, text, paper)


def llm_candidate(paper) -> bool:
    if (paper.impact_score or 0) < LLM_MIN_IMPACT:
        return False
    _, text = _paper_text(paper)
    return bool(re.search(r"\d", text) and _LLM_HINT.search(text))


# ---------------------------------------------------------------------------
# event calendar link (ForexFactory, backend/events.py)
# ---------------------------------------------------------------------------
_FF_GEO = [("german", "DE"), ("french", "FR"), ("spanish", "ES"), ("italian", "IT"), ("tokyo", "JP-TKY")]


def ff_indicator(title: str, ccy: str) -> tuple[Optional[str], Optional[str]]:
    """(indicator, geo) of a ForexFactory event title, or (None, None)."""
    t = title.lower()
    geo = next((g for w, g in _FF_GEO if t.startswith(w)), ZONE_AGGREGATE.get(ccy))
    q = "mom" if "m/m" in t else ("yoy" if "y/y" in t else ("qoq" if "q/q" in t else None))
    ind = None
    if "core pce" in t:
        ind = {"mom": "core_pce_mom", "yoy": "core_pce_yoy"}.get(q or "")
    elif "pce price" in t:
        ind = "pce_yoy" if q == "yoy" else None
    elif "core cpi" in t and "core-core" not in t:
        ind = {"mom": "core_cpi_mom", "yoy": "core_cpi_yoy"}.get(q or "")
    elif "cpi" in t or "hicp" in t:
        if q == "yoy":
            ind = "hicp_yoy" if ("hicp" in t and geo not in ("EA",)) else "cpi_yoy"
        elif q == "mom" and "hicp" not in t:
            ind = "cpi_mom"
    elif "non-farm employment change" in t:
        ind = "nfp"
    elif "unemployment claims" in t:
        ind = "jobless_claims"
    elif "unemployment change" in t:
        ind = "unemployment_change"
    elif "unemployment rate" in t:
        ind = "unemployment"
    elif "employment change" in t:
        ind = "employment_change"
    elif "average hourly earnings" in t or "average earnings" in t:
        ind = "wages_yoy" if ("y/y" in t or "3m/y" in t) else None
    elif "gdp" in t:
        ind = {"qoq": "gdp_qoq_saar" if ccy == "USD" else "gdp_qoq", "yoy": "gdp_yoy"}.get(q or "")
    elif "pmi" in t:
        ind = ("pmi_manufacturing" if "manufacturing" in t else
               "pmi_services" if ("services" in t or "non-manufacturing" in t) else
               "pmi_composite" if "composite" in t else None)
    elif "retail sales" in t and "core" not in t:
        ind = {"mom": "retail_sales_mom", "yoy": "retail_sales_yoy"}.get(q or "")
    elif re.search(r"funds rate|refinancing rate|deposit (?:facility )?rate|bank rate|policy rate|cash rate|overnight rate", t):
        ind = "policy_rate"
    return ind, (geo if ind else None)


def event_index(rows: list[dict]) -> dict[tuple, str]:
    """(zone, geo, indicator, UTC day) -> event_id."""
    idx = {}
    for e in rows:
        ind, geo = ff_indicator(e.get("title", ""), e.get("country", ""))
        if not ind:
            continue
        try:
            when = datetime.fromisoformat(e["date"])
        except Exception:
            continue
        day = when.astimezone(timezone.utc).date().isoformat()
        idx.setdefault((e.get("country"), geo, ind, day), event_id(e))
    return idx


def _load_event_index() -> dict:
    try:
        from . import events as events_mod
        rows, _err = events_mod.fetch()
        return event_index(rows)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# storage + runs
# ---------------------------------------------------------------------------
LLM_CAP_DEFAULT = 15


def _llm_cap() -> int:
    v = os.environ.get("MF_MAX_LLM_PER_RUN")
    return int(v) if v and v.strip() else LLM_CAP_DEFAULT


def store_facts(db, paper, facts: list[Fact], ev_idx: dict | None = None) -> int:
    """Replace the facts of `paper` (idempotent) and stamp it."""
    from .models import MacroFact
    db.query(MacroFact).filter(MacroFact.paper_id == paper.id).delete(synchronize_session=False)
    meta = _source_meta(paper)
    now = utcnow()
    day = _pub_date(paper).isoformat()
    for f in facts:
        inst = f.institution or meta.get("institution") or paper.source
        eid = None
        if ev_idx and f.kind in ("actual", "decision"):
            eid = ev_idx.get((f.zone, f.geo, f.indicator, day))
        db.add(MacroFact(paper_id=paper.id, source=paper.source, institution=inst,
                         published_date=paper.published_date, zone=f.zone, geo=f.geo,
                         indicator=f.indicator, kind=f.kind, value=f.value, unit=f.unit,
                         period=f.period or "", horizon=f.horizon, quote=f.quote, method=f.method,
                         confidence=f.confidence, event_id=eid, created_at=now))
    paper.macro_facts_at = now
    return len(facts)


def extract_recent(db, days: int = 7, *, force: bool = False, use_llm: bool = True,
                   llm_cap: Optional[int] = None, verbose: bool = False) -> dict:
    """Compute facts for papers published in the last `days` days that have
    none yet (`force`: recompute all of them). Pass 2 runs on at most
    `llm_cap` high-impact papers the rules found nothing in."""
    from sqlalchemy import or_
    from .models import Paper
    now = utcnow()
    q = (db.query(Paper).filter(Paper.published_date >= now - timedelta(days=days))
         .filter(Paper.published_date <= now + timedelta(days=1)))
    if not force:
        q = q.filter(or_(Paper.macro_facts_at.is_(None), Paper.macro_facts_at < Paper.analyzed_at))
    papers = q.order_by(Paper.published_date.desc()).all()
    cap = _llm_cap() if llm_cap is None else llm_cap
    ev_idx = _load_event_index()
    stats = {"papers": len(papers), "with_facts": 0, "facts": 0, "llm_calls": 0, "llm_facts": 0}
    empty_high = []
    for i, p in enumerate(papers):
        try:
            facts = extract_rules(p)
        except Exception as e:                      # one bad paper never stops the run
            print(f"    [!] macro-facts rules failed for {p.id}: {e}", flush=True)
            facts = []
        if not facts and use_llm and llm_candidate(p):
            empty_high.append(p)
        n = store_facts(db, p, facts, ev_idx)
        stats["facts"] += n
        stats["with_facts"] += bool(n)
        if verbose and n:
            print(f"    [mf] {n:2d} {p.source[:20]:20s} {p.title[:70]}", flush=True)
        if i % 200 == 199:
            db.commit()
    db.commit()
    if use_llm and cap > 0 and empty_high:
        import httpx
        empty_high.sort(key=lambda p: -(p.impact_score or 0))
        with httpx.Client(timeout=90.0) as client:
            for p in empty_high[:cap]:
                facts = extract_llm(p, client=client)
                stats["llm_calls"] += 1
                if facts:
                    store_facts(db, p, facts, ev_idx)
                    stats["llm_facts"] += len(facts)
                    stats["facts"] += len(facts)
                    stats["with_facts"] += 1
                db.commit()
    relink_events(db, ev_idx=ev_idx)
    return stats


def relink_events(db, days: int = 10, ev_idx: dict | None = None) -> int:
    """Attach event ids to recent actual/decision facts that have none."""
    from .models import MacroFact
    ev_idx = _load_event_index() if ev_idx is None else ev_idx
    if not ev_idx:
        return 0
    n = 0
    rows = (db.query(MacroFact).filter(MacroFact.event_id.is_(None))
            .filter(MacroFact.kind.in_(("actual", "decision")))
            .filter(MacroFact.published_date >= utcnow() - timedelta(days=days)).all())
    for r in rows:
        eid = ev_idx.get((r.zone, r.geo, r.indicator, r.published_date.date().isoformat()))
        if eid:
            r.event_id = eid
            n += 1
    db.commit()
    return n


# ---------------------------------------------------------------------------
# read side: /api/macro/facts and /api/macro/board
# ---------------------------------------------------------------------------
def fact_dict(r) -> dict:
    return {
        "id": r.id, "paper_id": r.paper_id, "ref": "RP-" + r.paper_id.replace("-", "")[:8],
        "source": r.source, "institution": r.institution, "published_date": iso_z(r.published_date),
        "zone": r.zone, "geo": r.geo, "indicator": r.indicator, "kind": r.kind, "value": r.value,
        "unit": r.unit, "period": r.period or None, "horizon": r.horizon, "quote": r.quote,
        "method": r.method, "confidence": r.confidence, "event_id": r.event_id,
    }


def query_facts(db, *, zone=None, indicator=None, kind=None, geo=None, days: int = 30,
                paper_id=None, limit: int = 200):
    from sqlalchemy import desc
    from .models import MacroFact, Paper
    now = utcnow()
    q = (db.query(MacroFact, Paper.source_url, Paper.pdf_url, Paper.title)
         .join(Paper, Paper.id == MacroFact.paper_id)
         .filter(MacroFact.published_date >= now - timedelta(days=days))
         .filter(MacroFact.published_date <= now + timedelta(days=1)))
    if zone:
        q = q.filter(MacroFact.zone == zone.upper())
    if indicator:
        q = q.filter(MacroFact.indicator == indicator)
    if kind:
        q = q.filter(MacroFact.kind == kind)
    if geo:
        q = q.filter(MacroFact.geo == geo.upper())
    if paper_id:
        q = q.filter(MacroFact.paper_id == paper_id)
    rows = q.order_by(desc(MacroFact.published_date), MacroFact.id).limit(limit).all()
    out = []
    for r, su, pu, title in rows:
        d = fact_dict(r)
        d["url"] = su or pu
        d["title"] = title
        out.append(d)
    return out


BOARD_METHOD = {
    "scope": "Facts quoted in articles published in the window; every value carries its exact quote.",
    "last_actual": "actual fact with the latest period (then the most confident, then the latest article); `confirmations` = "
                   "articles quoting the same value for that period, `conflicts` = other values quoted for it.",
    "consensus": "latest consensus quoted for the same period as last_actual (else the latest one).",
    "surprise": "last_actual.value - consensus.value, only when both share period and unit.",
    "forecasts": "per target period: the latest forecast of each institution; "
                 "median = statistics.median of those values; dispersion = max - min (0 with one forecast).",
    "computations": "median, dispersion and surprise are the only computed numbers; nothing else is derived.",
}


def _cite(d: dict) -> dict:
    keys = ("value", "unit", "period", "horizon", "quote", "institution", "source", "published_date",
            "paper_id", "ref", "url", "method", "confidence", "event_id")
    return {k: d.get(k) for k in keys}


def _pkey(p: Optional[str]) -> str:
    return p or ""


def build_board(db, *, days: int = 14, zones: Optional[list[str]] = None) -> dict:
    facts = query_facts(db, days=days, limit=100000)
    if zones:
        zs = {z.upper() for z in zones}
        facts = [f for f in facts if f["zone"] in zs]
    groups: dict[tuple, list[dict]] = {}
    for f in facts:
        groups.setdefault((f["zone"], f["geo"], f["indicator"], f["unit"]), []).append(f)
    rows = []
    for (zone, geo, ind, unit), fs in groups.items():
        by_kind = {k: [f for f in fs if f["kind"] == k] for k in KINDS}
        row = {"zone": zone, "geo": geo, "indicator": ind, "family": INDICATORS[ind][0], "unit": unit,
               "last_actual": None, "consensus": None, "surprise": None, "previous": None,
               "decision": None, "forecasts": []}
        acts = by_kind["actual"]
        if acts:
            top = max(acts, key=lambda f: (_pkey(f["period"]), f["confidence"], f["published_date"] or ""))
            same = [f for f in acts if _pkey(f["period"]) == _pkey(top["period"])]
            vals = sorted({f["value"] for f in same if f["value"] != top["value"]})
            row["last_actual"] = {**_cite(top),
                                  "confirmations": len({f["paper_id"] for f in same if f["value"] == top["value"]}),
                                  "conflicts": vals}
            cons = [f for f in by_kind["consensus"] if _pkey(f["period"]) == _pkey(top["period"]) and top["period"]]
            if cons:
                c = max(cons, key=lambda f: (f["published_date"] or "", f["confidence"]))
                row["consensus"] = _cite(c)
                row["surprise"] = round(top["value"] - c["value"], 6)
        if row["consensus"] is None and by_kind["consensus"]:
            c = max(by_kind["consensus"], key=lambda f: (_pkey(f["period"]), f["published_date"] or ""))
            row["consensus"] = _cite(c)
        if by_kind["previous"]:
            row["previous"] = _cite(max(by_kind["previous"], key=lambda f: (f["published_date"] or "", f["confidence"])))
        if by_kind["decision"]:
            row["decision"] = _cite(max(by_kind["decision"], key=lambda f: (_pkey(f["period"]), f["published_date"] or "")))
        per: dict[str, dict[str, dict]] = {}
        for f in sorted(by_kind["forecast"], key=lambda f: f["published_date"] or ""):
            per.setdefault(_pkey(f["period"]), {})[(f["institution"] or "?").lower()] = f   # latest per institution
        for period, by_inst in sorted(per.items()):
            vals = [f["value"] for f in by_inst.values()]
            row["forecasts"].append({
                "period": period or None, "horizon": period_end(period),
                "count": len(vals), "median": statistics.median(vals),
                "dispersion": round(max(vals) - min(vals), 6),
                "institutions": sorted({f["institution"] for f in by_inst.values() if f["institution"]}),
                "values": [_cite(f) for f in by_inst.values()],
            })
        rows.append(row)
    fam_order = {"inflation": 0, "policy_rate": 1, "employment": 2, "growth": 3, "activity": 4}
    rows.sort(key=lambda r: (r["zone"], fam_order.get(r["family"], 9), r["geo"] != ZONE_AGGREGATE.get(r["zone"]),
                             r["geo"], r["indicator"]))
    coverage: dict[str, dict[str, list[str]]] = {}
    for r in rows:
        if r["last_actual"] or r["consensus"] or r["decision"] or r["forecasts"] or r["previous"]:
            coverage.setdefault(r["zone"], {}).setdefault(r["family"], [])
            if r["indicator"] not in coverage[r["zone"]][r["family"]]:
                coverage[r["zone"]][r["family"]].append(r["indicator"])
    return {"window_days": days, "generated_utc": iso_z(utcnow()), "facts_in_window": len(facts),
            "method": BOARD_METHOD, "coverage": coverage, "rows": rows}
