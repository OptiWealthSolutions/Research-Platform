"""Single source of truth for the aggregator's provenance.

Like DataTradingPro's `_source` + `url` model, every paper we store keeps a link
to its origin. This catalog maps each `Paper.source` name to where it really
comes from: the upstream feed/portal, what *kind* of institution it is
(central bank / sell-side desk / multilateral / academic) and which **monetary
zone** it speaks to. The ingester reads the `rss`/`scrape` entries; the API
serves the whole catalog as the provenance map and powers the Central Banks
(by zone) and Desks (sell-side) navigation.

`kind`:
  - "rss"     -> auto-ingested from a live RSS/Atom feed
  - "scrape"  -> auto-ingested by scraping a listing page (best-effort)
  - "portal"  -> not auto-ingested (JS-gated / login-walled); registered so the
                 UI can still surface the desk and link straight to its portal
"""

# Monetary-zone metadata used by the Central Banks view.
ZONES = {
    "USD": {"label": "United States",  "ccy": "USD", "flag": "", "authority": "Federal Reserve"},
    "EUR": {"label": "Euro Area",      "ccy": "EUR", "flag": "", "authority": "ECB / Eurosystem"},
    "GBP": {"label": "United Kingdom",  "ccy": "GBP", "flag": "", "authority": "Bank of England"},
    "JPY": {"label": "Japan",           "ccy": "JPY", "flag": "", "authority": "Bank of Japan"},
    "CHF": {"label": "Switzerland",     "ccy": "CHF", "flag": "", "authority": "Swiss National Bank"},
    "CAD": {"label": "Canada",          "ccy": "CAD", "flag": "", "authority": "Bank of Canada"},
    "SEK": {"label": "Sweden",          "ccy": "SEK", "flag": "", "authority": "Sveriges Riksbank"},
    "INR": {"label": "India",           "ccy": "INR", "flag": "", "authority": "Reserve Bank of India"},
    "GLB": {"label": "Global / Multilateral", "ccy": "GLB", "flag": "", "authority": "BIS / IMF"},
}
ZONE_ORDER = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "SEK", "INR", "GLB"]

CATEGORIES = {
    "central_bank": "Central Banks",
    "commercial_bank": "Commercial Banks",
    "fund": "Funds & Asset Managers",
    "multilateral": "Multilateral",
    "academic": "Academic",
}


def _s(name, institution, domain, *, kind, category, zone, feed=None, portal=None, scrape=None):
    return {
        "name": name, "institution": institution, "domain": domain,
        "kind": kind, "category": category, "zone": zone,
        "feed": feed, "portal": portal, "scrape": scrape,
    }


# Order matters only for display. `name` MUST match Paper.source written at ingest.
CATALOG = [
    # ============================ CENTRAL BANKS ============================
    # --- USD / Federal Reserve ---
    _s("Fed Press", "Federal Reserve Board", "federalreserve.gov", kind="rss",
       category="central_bank", zone="USD", feed="https://www.federalreserve.gov/feeds/press_all.xml",
       portal="https://www.federalreserve.gov/newsevents/pressreleases.htm"),
    _s("Fed Speeches", "Federal Reserve Board", "federalreserve.gov", kind="rss",
       category="central_bank", zone="USD", feed="https://www.federalreserve.gov/feeds/speeches.xml",
       portal="https://www.federalreserve.gov/newsevents/speeches.htm"),
    _s("Fed Board (FEDS)", "Federal Reserve Board", "federalreserve.gov", kind="rss",
       category="central_bank", zone="USD", feed="https://www.federalreserve.gov/feeds/feds.xml",
       portal="https://www.federalreserve.gov/econres/feds/index.htm"),
    _s("Fed Board (IFDP)", "Federal Reserve Board", "federalreserve.gov", kind="rss",
       category="central_bank", zone="USD", feed="https://www.federalreserve.gov/feeds/ifdp.xml",
       portal="https://www.federalreserve.gov/econres/ifdp/index.htm"),
    _s("Fed NY Liberty St", "Fed Reserve Bank of New York", "newyorkfed.org", kind="rss",
       category="central_bank", zone="USD", feed="https://libertystreeteconomics.newyorkfed.org/feed/",
       portal="https://libertystreeteconomics.newyorkfed.org/"),
    _s("Fed Atlanta Macroblog", "Fed Reserve Bank of Atlanta", "atlantafed.org", kind="rss",
       category="central_bank", zone="USD", feed="https://www.atlantafed.org/rss/macroblog",
       portal="https://www.atlantafed.org/blogs/macroblog"),
    # --- EUR / ECB + Eurosystem NCBs ---
    _s("ECB Press", "European Central Bank", "ecb.europa.eu", kind="rss",
       category="central_bank", zone="EUR", feed="https://www.ecb.europa.eu/rss/press.html",
       portal="https://www.ecb.europa.eu/press/html/index.en.html"),
    _s("ECB Blog", "European Central Bank", "ecb.europa.eu", kind="rss",
       category="central_bank", zone="EUR", feed="https://www.ecb.europa.eu/rss/blog.html",
       portal="https://www.ecb.europa.eu/press/blog/html/index.en.html"),
    _s("ECB Research", "European Central Bank", "ecb.europa.eu", kind="rss",
       category="central_bank", zone="EUR", feed="https://www.ecb.europa.eu/rss/pub.html",
       portal="https://www.ecb.europa.eu/pub/research/html/index.en.html"),
    _s("Bundesbank", "Deutsche Bundesbank", "bundesbank.de", kind="scrape",
       category="central_bank", zone="EUR",
       portal="https://www.bundesbank.de/en/publications/research/discussion-papers",
       scrape={"url": "https://www.bundesbank.de/en/publications/research/discussion-papers",
               "href_contains": "/discussion-papers/", "base": "https://www.bundesbank.de"}),
    _s("Banque de France", "Banque de France", "banque-france.fr", kind="scrape",
       category="central_bank", zone="EUR",
       portal="https://www.banque-france.fr/en/publications-and-statistics/publications/working-papers",
       scrape={"url": "https://www.banque-france.fr/en/publications-and-statistics/publications/working-papers",
               "href_contains": "/working-paper", "base": "https://www.banque-france.fr"}),
    # --- GBP / Bank of England ---
    _s("Bank of England", "Bank of England", "bankofengland.co.uk", kind="rss",
       category="central_bank", zone="GBP", feed="https://www.bankofengland.co.uk/rss/publications",
       portal="https://www.bankofengland.co.uk/research"),
    _s("Bank of England News", "Bank of England", "bankofengland.co.uk", kind="rss",
       category="central_bank", zone="GBP", feed="https://www.bankofengland.co.uk/rss/news",
       portal="https://www.bankofengland.co.uk/news"),
    # --- JPY / Bank of Japan ---
    _s("Bank of Japan", "Bank of Japan", "boj.or.jp", kind="rss",
       category="central_bank", zone="JPY", feed="https://www.boj.or.jp/en/rss/whatsnew.xml",
       portal="https://www.boj.or.jp/en/index.htm"),
    # --- CHF / Swiss National Bank ---
    _s("SNB Press", "Swiss National Bank", "snb.ch", kind="rss",
       category="central_bank", zone="CHF", feed="https://www.snb.ch/public/en/rss/news",
       portal="https://www.snb.ch/en/the-snb/mandates-goals/press-releases"),
    _s("SNB Speeches", "Swiss National Bank", "snb.ch", kind="rss",
       category="central_bank", zone="CHF", feed="https://www.snb.ch/public/en/rss/speeches",
       portal="https://www.snb.ch/en/services-events/speeches"),
    # --- CAD / Bank of Canada ---
    _s("Bank of Canada", "Bank of Canada", "bankofcanada.ca", kind="rss",
       category="central_bank", zone="CAD", feed="https://www.bankofcanada.ca/feed/",
       portal="https://www.bankofcanada.ca/press/"),
    # --- SEK / Sveriges Riksbank ---
    _s("Riksbank Press", "Sveriges Riksbank", "riksbank.se", kind="rss",
       category="central_bank", zone="SEK", feed="https://www.riksbank.se/en-gb/rss/press-releases/",
       portal="https://www.riksbank.se/en-gb/press-and-published/notices-and-press-releases/"),
    _s("Riksbank Speeches", "Sveriges Riksbank", "riksbank.se", kind="rss",
       category="central_bank", zone="SEK", feed="https://www.riksbank.se/en-gb/rss/speeches/",
       portal="https://www.riksbank.se/en-gb/press-and-published/speeches-and-presentations/"),
    # --- INR / Reserve Bank of India ---
    _s("RBI Press", "Reserve Bank of India", "rbi.org.in", kind="rss",
       category="central_bank", zone="INR", feed="https://www.rbi.org.in/pressreleases_rss.xml",
       portal="https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"),

    # ============================ MULTILATERAL ============================
    _s("BIS Hub", "Bank for International Settlements", "bis.org", kind="rss",
       category="multilateral", zone="GLB", feed="https://www.bis.org/doclist/reshub_papers.rss",
       portal="https://www.bis.org/innovation_hub/index.htm"),
    _s("BIS WP", "Bank for International Settlements", "bis.org", kind="rss",
       category="multilateral", zone="GLB", feed="https://www.bis.org/doclist/bis_fsi_publs.rss",
       portal="https://www.bis.org/publ/index.htm"),
    _s("IMF Working Papers", "International Monetary Fund", "imf.org", kind="rss",
       category="multilateral", zone="GLB",
       feed="https://www.imf.org/en/Publications/RSS?language=eng&series=IMF%20Working%20Papers",
       portal="https://www.imf.org/en/Publications/WP"),

    # ============================ COMMERCIAL BANKS ============================
    # Live RSS -> auto-ingested.
    _s("ING THINK", "ING", "think.ing.com", kind="rss", category="commercial_bank", zone="EUR",
       feed="https://think.ing.com/rss/", portal="https://think.ing.com/"),
    _s("Wells Fargo Stories", "Wells Fargo", "wf.com", kind="rss", category="commercial_bank", zone="USD",
       feed="https://stories.wf.com/feed/", portal="https://stories.wf.com/"),
    # Server-rendered insight listings -> article-scraped (title + date + link).
    _s("Goldman Sachs", "Goldman Sachs", "goldmansachs.com", kind="article_scrape", category="commercial_bank", zone="USD",
       portal="https://www.goldmansachs.com/insights/",
       scrape={"url": "https://www.goldmansachs.com/insights", "base": "https://www.goldmansachs.com",
               "link_re": r"/insights/(articles|goldman-sachs-exchanges|talks-at-gs)/[a-z0-9]",
               "date_mode": "parent", "require_date": True, "min_title": 22}),
    _s("J.P. Morgan", "J.P. Morgan", "jpmorgan.com", kind="article_scrape", category="commercial_bank", zone="USD",
       portal="https://www.jpmorgan.com/insights",
       scrape={"url": "https://www.jpmorgan.com/insights", "base": "https://www.jpmorgan.com",
               "link_re": r"/insights/(markets-and-economy|global-research|business|cybersecurity|payments)/.+/.+",
               "date_mode": "parent", "require_date": True, "min_title": 22}),
    _s("HSBC", "HSBC", "hsbc.com", kind="article_scrape", category="commercial_bank", zone="GLB",
       portal="https://www.gbm.hsbc.com/en-gb/insights",
       scrape={"url": "https://www.gbm.hsbc.com/en-gb/insights", "base": "https://www.gbm.hsbc.com",
               "link_re": r"/en-gb/insights/[a-z][a-z0-9-]{6,}$",
               "date_mode": "parent", "require_date": True, "min_title": 18}),
    # JS-gated / login-walled desks: linked for navigation, not auto-pulled.
    _s("SEB Research", "SEB", "research.sebgroup.com", kind="portal", category="commercial_bank", zone="SEK",
       portal="https://research.sebgroup.com/"),
    # Public insights are JS-rendered -> headless (Playwright). The login-walled
    # research.danskebank.com trade-call product is NOT scraped.
    _s("Danske Bank", "Danske Bank", "danskebank.com", kind="article_scrape", category="commercial_bank", zone="EUR",
       portal="https://danskebank.com/news-and-insights",
       scrape={"url": "https://danskebank.com/news-and-insights", "base": "https://danskebank.com",
               "link_re": r"/news-and-insights/[a-z][a-z0-9-]{8,}", "date_mode": "parent",
               "render": True, "wait": 4, "require_date": True, "min_title": 18,
               "exclude_re": r"buy-?back|managers'? transaction|transactions in connection|share repurchase"}),
    _s("MUFG Research", "MUFG", "mufgresearch.com", kind="portal", category="commercial_bank", zone="JPY",
       portal="https://www.mufgresearch.com/"),
    _s("Westpac IQ", "Westpac", "westpaciq.com.au", kind="portal", category="commercial_bank", zone="GLB",
       portal="https://www.westpaciq.com.au/"),
    _s("Nordea Research", "Nordea", "corporate.nordea.com", kind="portal", category="commercial_bank", zone="EUR",
       portal="https://corporate.nordea.com/article/insights"),
    _s("Natixis CIB", "Natixis", "research.natixis.com", kind="portal", category="commercial_bank", zone="EUR",
       portal="https://research.natixis.com/"),
    _s("Scotiabank GBM", "Scotiabank", "scotiabank.com", kind="portal", category="commercial_bank", zone="CAD",
       portal="https://www.gbm.scotiabank.com/en/insights.html"),
    _s("KBC Research", "KBC", "kbc.com", kind="portal", category="commercial_bank", zone="EUR",
       portal="https://www.kbc.com/en/economics.html"),
    _s("UniCredit Research", "UniCredit", "unicreditgroup.eu", kind="portal", category="commercial_bank", zone="EUR",
       portal="https://www.unicreditresearch.eu/"),
    _s("CIBC Economics", "CIBC", "economics.cibccm.com", kind="portal", category="commercial_bank", zone="CAD",
       portal="https://economics.cibccm.com/"),
    _s("Societe Generale", "Societe Generale", "societegenerale.com", kind="portal", category="commercial_bank", zone="EUR",
       portal="https://wholesale.banking.societegenerale.com/en/insights/"),
    _s("Standard Chartered", "Standard Chartered", "sc.com", kind="portal", category="commercial_bank", zone="GLB",
       portal="https://www.sc.com/en/banking/banking-for-companies/financial-markets/insights/"),
    _s("Deutsche Bank Research", "Deutsche Bank", "db.com", kind="portal", category="commercial_bank", zone="EUR",
       portal="https://www.dbresearch.com/"),
    _s("Barclays IB", "Barclays", "barclays", kind="portal", category="commercial_bank", zone="GLB",
       portal="https://www.cib.barclays/our-insights.html"),
    _s("BNP Paribas Research", "BNP Paribas", "bnpparibas.com", kind="portal", category="commercial_bank", zone="EUR",
       portal="https://economic-research.bnpparibas.com/"),
    _s("Morgan Stanley Ideas", "Morgan Stanley", "morganstanley.com", kind="portal", category="commercial_bank", zone="USD",
       portal="https://www.morganstanley.com/ideas"),
    _s("Citi", "Citi", "citigroup.com", kind="portal", category="commercial_bank", zone="USD",
       portal="https://www.citigroup.com/global/insights"),
    _s("RBC Economics", "RBC", "rbc.com", kind="portal", category="commercial_bank", zone="CAD",
       portal="https://www.rbc.com/en/thought-leadership/economics/"),
    _s("TD Economics", "TD Bank", "td.com", kind="portal", category="commercial_bank", zone="CAD",
       portal="https://economics.td.com/"),
    _s("Credit Agricole Research", "Credit Agricole", "credit-agricole.com", kind="portal", category="commercial_bank", zone="EUR",
       portal="https://www.credit-agricole.com/en/finance/economic-research"),
    _s("Nomura Research", "Nomura", "nomuraconnects.com", kind="portal", category="commercial_bank", zone="JPY",
       portal="https://www.nomuraconnects.com/focused-thinking/"),
    _s("Commerzbank Research", "Commerzbank", "commerzbank.com", kind="portal", category="commercial_bank", zone="EUR",
       portal="https://www.commerzbank.com/en/hauptnavigation/presse/research/research.html"),
    _s("BBVA Research", "BBVA", "bbvaresearch.com", kind="portal", category="commercial_bank", zone="EUR",
       portal="https://www.bbvaresearch.com/en/"),
    _s("Intesa Sanpaolo", "Intesa Sanpaolo", "group.intesasanpaolo.com", kind="portal", category="commercial_bank", zone="EUR",
       portal="https://group.intesasanpaolo.com/en/research"),
    _s("Lloyds Commercial Banking", "Lloyds Bank", "lloydsbank.com", kind="portal", category="commercial_bank", zone="GBP",
       portal="https://www.lloydsbankcommercial.com/insight/"),
    _s("NatWest Markets", "NatWest", "natwest.com", kind="portal", category="commercial_bank", zone="GBP",
       portal="https://www.natwest.com/corporates/insights.html"),
    _s("ANZ Research", "ANZ", "anz.com", kind="portal", category="commercial_bank", zone="GLB",
       portal="https://www.anz.com.au/institutional/insights/"),
    _s("NAB Markets", "National Australia Bank", "nab.com.au", kind="portal", category="commercial_bank", zone="GLB",
       portal="https://business.nab.com.au/markets/"),
    _s("Macquarie", "Macquarie", "macquarie.com", kind="portal", category="commercial_bank", zone="GLB",
       portal="https://www.macquarie.com/au/en/insights.html"),

    # ============================ FUNDS & ASSET MANAGERS ============================
    # Server-rendered insight listings -> article-scraped.
    _s("Man Institute", "Man Group", "man.com", kind="article_scrape", category="fund", zone="GLB",
       portal="https://www.man.com/insights",
       scrape={"url": "https://www.man.com/insights", "base": "https://www.man.com",
               "link_re": r"man\.com/insights/[a-z0-9]", "date_mode": "parent",
               "require_date": True, "min_title": 18}),
    _s("Robeco", "Robeco", "robeco.com", kind="article_scrape", category="fund", zone="GLB",
       portal="https://www.robeco.com/en-int/insights",
       scrape={"url": "https://www.robeco.com/en-int/insights", "base": "https://www.robeco.com",
               "link_re": r"/insights/20\d\d/\d{2}/[a-z]", "date_mode": "url",
               "require_date": True, "min_title": 20}),
    _s("Amundi Research", "Amundi", "amundi.com", kind="article_scrape", category="fund", zone="EUR",
       portal="https://research-center.amundi.com/",
       scrape={"url": "https://research-center.amundi.com/", "base": "https://research-center.amundi.com",
               "link_re": r"/article/[a-z]", "date_mode": "text", "dayfirst": True,
               "require_date": True, "min_title": 18}),
    # JS-gated / login-walled managers: linked for navigation, not auto-pulled.
    _s("BlackRock Investment Institute", "BlackRock", "blackrock.com", kind="portal", category="fund", zone="GLB",
       portal="https://www.blackrock.com/corporate/insights/blackrock-investment-institute"),
    _s("PIMCO", "PIMCO", "pimco.com", kind="portal", category="fund", zone="GLB",
       portal="https://www.pimco.com/en-us/insights"),
    _s("Schroders", "Schroders", "schroders.com", kind="article_scrape", category="fund", zone="GLB",
       portal="https://www.schroders.com/en/global/individual/insights/",
       scrape={"url": "https://www.schroders.com/en/global/individual/insights/", "base": "https://www.schroders.com",
               "link_re": r"/insights/[a-z][a-z0-9-]{12,}", "date_mode": "parent",
               "render": True, "wait": 5, "require_date": True, "min_title": 16}),
    _s("Invesco", "Invesco", "invesco.com", kind="portal", category="fund", zone="GLB",
       portal="https://www.invesco.com/corporate/en/insights.html"),
    _s("Vanguard", "Vanguard", "vanguard.com", kind="portal", category="fund", zone="USD",
       portal="https://corporate.vanguard.com/content/corporatesite/us/en/corp/articles.html"),
    _s("Fidelity International", "Fidelity", "fidelityinternational.com", kind="portal", category="fund", zone="GLB",
       portal="https://www.fidelityinternational.com/insights/"),
    _s("T. Rowe Price", "T. Rowe Price", "troweprice.com", kind="portal", category="fund", zone="USD",
       portal="https://www.troweprice.com/financial-intermediary/us/en/insights.html"),
    _s("Janus Henderson", "Janus Henderson", "janushenderson.com", kind="portal", category="fund", zone="GLB",
       portal="https://www.janushenderson.com/en-us/investor/insights/"),
    _s("Franklin Templeton", "Franklin Templeton", "franklintempleton.com", kind="portal", category="fund", zone="USD",
       portal="https://www.franklintempleton.com/articles"),
    _s("AllianceBernstein", "AllianceBernstein", "alliancebernstein.com", kind="portal", category="fund", zone="USD",
       portal="https://www.alliancebernstein.com/corporate/en/insights.html"),
    _s("State Street GA", "State Street Global Advisors", "ssga.com", kind="portal", category="fund", zone="USD",
       portal="https://www.ssga.com/us/en/intermediary/insights"),
    _s("Capital Group", "Capital Group", "capitalgroup.com", kind="portal", category="fund", zone="USD",
       portal="https://www.capitalgroup.com/advisor/insights.html"),
    _s("AQR", "AQR Capital", "aqr.com", kind="portal", category="fund", zone="USD",
       portal="https://www.aqr.com/Insights"),

    # ============================ ACADEMIC ============================
    _s("NBER", "National Bureau of Economic Research", "nber.org", kind="rss", category="academic", zone="USD",
       feed="https://www.nber.org/rss/new.xml", portal="https://www.nber.org/papers"),
    _s("Macro (NEP)", "RePEc / NEP", "repec.org", kind="rss", category="academic", zone="GLB",
       feed="https://nep.repec.org/rss/nep-mac.rss.xml", portal="https://nep.repec.org/"),
    _s("Finance (NEP)", "RePEc / NEP", "repec.org", kind="rss", category="academic", zone="GLB",
       feed="https://nep.repec.org/rss/nep-fin.rss.xml", portal="https://nep.repec.org/"),
    _s("Monetary (NEP)", "RePEc / NEP", "repec.org", kind="rss", category="academic", zone="GLB",
       feed="https://nep.repec.org/rss/nep-mon.rss.xml", portal="https://nep.repec.org/"),
    _s("Banking (NEP)", "RePEc / NEP", "repec.org", kind="rss", category="academic", zone="GLB",
       feed="https://nep.repec.org/rss/nep-ban.rss.xml", portal="https://nep.repec.org/"),
    _s("Elsevier/SSRN", "Elsevier / SSRN", "ssrn.com", kind="portal", category="academic", zone="GLB",
       portal="https://www.ssrn.com/"),
]

# --- derived lookups -------------------------------------------------------
META = {s["name"]: s for s in CATALOG}


def rss_sources():
    """name -> feed url, for the ingester."""
    return {s["name"]: s["feed"] for s in CATALOG if s["kind"] == "rss" and s["feed"]}


def scrape_sources():
    """list of listing-scrape configs (legacy anchor scraper) for the ingester."""
    return [{"source_name": s["name"], **s["scrape"]} for s in CATALOG
            if s["kind"] == "scrape" and s["scrape"]]


def article_scrape_sources():
    """list of rich article-scrape configs (commercial banks / funds)."""
    return [{"name": s["name"], "zone": s["zone"], **s["scrape"]} for s in CATALOG
            if s["kind"] == "article_scrape" and s["scrape"]]


def category_of(source_name):
    s = META.get(source_name)
    return s["category"] if s else "academic"


def zone_of(source_name):
    s = META.get(source_name)
    return s["zone"] if s else "GLB"


def sources_in_category(category):
    return [s["name"] for s in CATALOG if s["category"] == category]
