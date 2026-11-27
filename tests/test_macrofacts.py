"""Tests of backend/macrofacts.py: golden rule, zone dictionary, dedup, LLM
JSON validation, rule extraction on real wire phrasings, storage and board.

Run:  /opt/anaconda3/bin/python3 -m pytest tests -q
Everything runs on an in-memory SQLite database; nothing touches research.db.
"""
import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("RP_DB_PATH", ":memory:")

from backend import fxcalls  # noqa: E402
from backend import macrofacts as mf  # noqa: E402
from backend.timeutil import utcnow  # noqa: E402


def paper(title, abstract="", source="ForexLive", days_ago=0, impact=50.0, pid="p1"):
    return SimpleNamespace(id=pid, title=title, abstract=abstract, full_text=None, source=source,
                           published_date=utcnow() - timedelta(days=days_ago), created_at=utcnow(),
                           impact_score=impact, analyzed_at=None, macro_facts_at=None,
                           source_url="https://example.org/a", pdf_url=None)


def facts_of(title, abstract="", **kw):
    return {(f.geo, f.indicator, f.kind, f.value) for f in mf.extract_rules(paper(title, abstract, **kw))}


# ---------------------------------------------------------------------------
# golden rule
# ---------------------------------------------------------------------------
class TestGoldenRule:
    def test_value_written_in_quote(self):
        assert mf.quote_contains_value("inflation rose to 3.2% in October", 3.2)
        assert mf.quote_contains_value("payrolls rose by 119,000", 119000)
        assert mf.quote_contains_value("France Q3 final GDP -0.2% vs -0.1% q/q prelim", -0.2)
        assert mf.quote_contains_value("rate in a range between 3.75%-4%", 4)

    def test_value_absent_or_partial(self):
        assert not mf.quote_contains_value("inflation rose to 13.25%", 3.2)   # not a substring match
        assert not mf.quote_contains_value("inflation rose to 3.25%", 3.2)
        assert not mf.quote_contains_value("payrolls rose by 119,000", 119)    # no unit conversion
        assert not mf.quote_contains_value("GDP drops 0.2%", -0.2)             # sign not written
        assert not mf.quote_contains_value("between 3.75%-4%", -4)             # a dash is not a sign
        assert not mf.quote_contains_value("", 1.0)

    def test_accept_requires_exact_excerpt(self):
        text = "UK inflation rose to 3.6% in October."
        ok = mf.Fact(zone="GBP", geo="UK", indicator="cpi_yoy", kind="actual", value=3.6, unit="%",
                     period="2026-10", quote="UK inflation rose to 3.6% in October", confidence=0.7)
        assert mf.accept(ok, text)
        assert not mf.accept(ok.model_copy(update={"value": 3.5}), text)
        assert not mf.accept(ok.model_copy(update={"quote": "UK inflation climbed to 3.6% in October"}), text)
        assert not mf.accept(ok.model_copy(update={"zone": "EUR"}), text)          # geo UK is GBP

    def test_every_extracted_fact_passes_it(self):
        p = paper("US October PCE core inflation +0.3% m/m vs +0.3% expected",
                  "Prior m/m +0.3%Core PCE +3.3% y/y vs +3.2% expectedPrior was 3.1% y/y"
                  "Headline PCE +3.8% y/y vs +3.8% expected (prior 3.6%)Deflator m/m +0.3% vs +0.3% expected")
        _, text = mf._paper_text(p)
        facts = mf.extract_rules(p)
        assert facts
        for f in facts:
            assert f.quote in text and mf.quote_contains_value(f.quote, f.value) and len(f.quote) <= 200

    def test_quote_is_cut_to_200_chars_around_the_number(self):
        long = ("Lorem ipsum dolor sit amet " * 12) + "Japan core CPI rose to 2.9% y/y in November " + ("and more words " * 12)
        facts = mf.extract_rules(paper("Japan data", long))
        f = next(x for x in facts if x.value == 2.9)
        assert len(f.quote) <= 200 and "2.9%" in f.quote


# ---------------------------------------------------------------------------
# zone dictionary
# ---------------------------------------------------------------------------
class TestZones:
    def test_zone_codes_are_fxcalls_codes(self):
        assert set(mf.ZONES) == set(fxcalls._CCY)
        assert set(mf.GEO_ZONE.values()) <= set(fxcalls._CCY)
        assert all(z in fxcalls._CCY for z in mf.ZONE_AGGREGATE)

    def test_country_words_agree_with_fxcalls(self):
        for word, geo in mf._GEO_WORDS.items():
            if geo and word in fxcalls._COUNTRY:
                assert mf.GEO_ZONE[geo] == fxcalls._COUNTRY[word], word

    @pytest.mark.parametrize("text,geo", [
        ("German inflation", "DE"), ("the euro area economy", "EA"), ("Tokyo core CPI", "JP-TKY"),
        ("U.S. payrolls", "US"), ("UK inflation", "UK"), ("Bank of Japan", "JP"), ("the ECB", "EA"),
    ])
    def test_geo_words(self, text, geo):
        assert mf._geo_hits(text)[-1][2] == geo

    def test_lowercase_us_is_not_the_united_states(self):
        assert mf._geo_hits("let us see inflation") == []

    def test_foreign_economy_blocks_attribution(self):
        assert facts_of("Brazil inflation rises to 4.6% in November") == set()
        assert facts_of("Zambia holds interest rate at 10.75% as inflation stays on target") == set()

    def test_national_print_keeps_its_geo(self):
        got = facts_of("Spain November preliminary CPI +4.1% vs +4.6% y/y expected")
        assert ("ES", "cpi_yoy", "actual", 4.1) in got and ("ES", "cpi_yoy", "consensus", 4.6) in got
        assert all(g == "ES" for g, *_ in got)


# ---------------------------------------------------------------------------
# rules on real wire phrasings
# ---------------------------------------------------------------------------
class TestRules:
    def test_vs_expected_prior(self):
        got = facts_of("Germany November unemployment change 9k vs 4k expected",
                       "Germany November unemployment change 9k vs 4k expectedPrior 4k"
                       "Germany November unemployment rate 6.4% vs 6.3% expectedPrior 6.4%")
        assert ("DE", "unemployment_change", "actual", 9) in got
        assert ("DE", "unemployment_change", "consensus", 4) in got
        assert ("DE", "unemployment", "actual", 6.4) in got
        assert ("DE", "unemployment", "consensus", 6.3) in got
        assert ("DE", "unemployment", "previous", 6.4) in got

    def test_period_and_previous(self):
        fs = mf.extract_rules(paper(
            "Breaking: US core PCE inflation rises to 3.3% YoY in October vs 3.2% expected",
            "Inflation in the United States (US), as measured by the change in the Personal Consumption "
            "Expenditures (PCE) Price Index, rose to 3.8% on a yearly basis in October, from a revised 3.7% "
            "in September."))
        by = {(f.indicator, f.kind): f for f in fs}
        assert by[("core_pce_yoy", "actual")].value == 3.3
        assert by[("core_pce_yoy", "actual")].period.endswith("-10")
        assert by[("core_pce_yoy", "consensus")].value == 3.2
        assert by[("pce_yoy", "actual")].value == 3.8
        assert by[("pce_yoy", "previous")].value == 3.7 and by[("pce_yoy", "previous")].period.endswith("-09")

    def test_bank_forecast_has_institution_and_horizon(self):
        fs = mf.extract_rules(paper("French economy sinks deeper into gloom",
                                    "We now expect growth of just 0.1% in 2026 and 0.4% in 2027.",
                                    source="ING THINK"))
        got = {(f.indicator, f.kind, f.value, f.period, f.horizon, f.institution) for f in fs}
        assert ("gdp_annual", "forecast", 0.1, "2026", "2026-12-31", "ING") in got
        assert ("gdp_annual", "forecast", 0.4, "2027", "2027-12-31", "ING") in got

    def test_rate_decision_range_keeps_upper_bound(self):
        fs = mf.extract_rules(paper("Fed minutes", "The Federal Reserve released minutes from its October policy "
                                    "meeting at which it voted to hold its benchmark interest rate in a range "
                                    "between 3.75%-4%.", source="CNBC Top News"))
        assert [(f.geo, f.indicator, f.kind, f.value) for f in fs] == [("US", "policy_rate", "decision", 4.0)]

    def test_market_pricing_is_consensus(self):
        got = facts_of("The BoJ is priced for a 70% chance of a December hike to 1.50%", source="FXStreet Analysis")
        assert got == {("JP", "policy_rate", "consensus", 1.5)}

    def test_blockers(self):
        assert facts_of("Japan services producer prices jump 4.2% in October") == set()
        assert facts_of("ECB Survey: One-year inflation expectations accelerate to 3.5% in October") == set()
        assert facts_of("Tokyo data", "Core-core CPI (excluding fresh food and energy): 2.5% y/y\nexpected 2.7%, prior 2.4%") == set()
        assert facts_of("Australia trimmed mean CPI jumps to 3.8% in October") == set()
        assert facts_of("BoJ must keep raising rates, as long as underlying inflation is around 2%") == set()
        assert facts_of("Fed's Williams sees inflation closer to the Fed's 2% target in 2027") == set()

    def test_year_on_year_after_the_number(self):
        assert ("AU", "cpi_yoy", "actual", 4.9) in facts_of(
            "Australia’s CPI surprise", "AUSTRALIA: Australia's October CPI rose to 4.9% year-on-year, driven by fuel.")
        assert facts_of("UK inflation to 1-year high of 3.6%") == {("UK", "cpi_yoy", "actual", 3.6)}

    def test_unwritten_sign_is_rejected(self):
        assert facts_of("France GDP drops 0.2% in Q3 as exports fall sharply") == set()
        assert ("FR", "gdp_qoq", "actual", -0.2) in facts_of("France Q3 final GDP -0.2% vs -0.1% q/q prelim")

    def test_preview_is_not_an_actual(self):
        got = facts_of("Australia CPI preview: 4.8% looks like the peak")
        assert not any(k == "actual" for _, _, k, _ in got)

    def test_money_and_market_moves_ignored(self):
        assert facts_of("US data", "Gold (XAU/USD) registers a 0.70% loss on Wednesday after the latest US "
                                   "inflation report was hotter than expected") == set()
        assert facts_of("Capex", "total capex at about A$59.5 billion in inflation-adjusted terms, 18.6% higher") == set()


# ---------------------------------------------------------------------------
# deduplication
# ---------------------------------------------------------------------------
class TestDedup:
    def _f(self, **kw):
        base = dict(zone="USD", geo="US", indicator="cpi_yoy", kind="actual", value=3.2, unit="%",
                    period="2026-10", quote="CPI 3.2%", confidence=0.6)
        base.update(kw)
        return mf.Fact(**base)

    def test_same_key_keeps_most_confident(self):
        out = mf.dedupe([self._f(), self._f(value=3.3, quote="CPI 3.3%", confidence=0.9), self._f(kind="consensus")])
        assert len(out) == 2
        assert next(f for f in out if f.kind == "actual").value == 3.3

    def test_geo_is_part_of_the_key(self):
        out = mf.dedupe([self._f(zone="EUR", geo="FR"), self._f(zone="EUR", geo="ES")])
        assert len(out) == 2

    def test_repeated_paragraph_gives_one_fact(self):
        line = "Spain November preliminary CPI +4.1% vs +4.6% y/y expected"
        fs = mf.extract_rules(paper(line, line + "\n" + line))
        keys = [(f.geo, f.indicator, f.kind, f.period) for f in fs]
        assert len(keys) == len(set(keys)) == 2

    def test_store_is_idempotent_and_unique(self, db):
        from backend.models import MacroFact, Paper
        p = Paper(id="pp1", title="Spain November preliminary CPI +4.1% vs +4.6% y/y expected", abstract="",
                  source="ForexLive", published_date=utcnow(), clean_title_hash="h1")
        db.add(p)
        db.commit()
        facts = mf.extract_rules(p)
        mf.store_facts(db, p, facts)
        db.commit()
        mf.store_facts(db, p, facts)
        db.commit()
        assert db.query(MacroFact).count() == len(facts) == 2
        from sqlalchemy.exc import IntegrityError
        r = db.query(MacroFact).first()
        db.add(MacroFact(paper_id=r.paper_id, zone=r.zone, geo=r.geo, indicator=r.indicator, kind=r.kind,
                         value=9.9, unit=r.unit, period=r.period, quote="x 9.9", method="regex", confidence=0.1))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


# ---------------------------------------------------------------------------
# LLM JSON validation
# ---------------------------------------------------------------------------
TEXT = "Tokyo November core CPI rises to 2.9% y/y, just shy of 3.0% expected. Prior 2.6%."


def good_fact(**kw):
    f = {"zone": "JPY", "geo": "JP-TKY", "indicator": "core_cpi_yoy", "kind": "actual", "value": 2.9,
         "unit": "%", "period": "2026-11", "quote": "Tokyo November core CPI rises to 2.9% y/y"}
    f.update(kw)
    return f


class TestLLMValidation:
    def test_valid_payload(self):
        out = mf.validate_llm_output(json.dumps({"facts": [good_fact(), good_fact(kind="consensus", value=3.0,
                                     quote="just shy of 3.0% expected")]}), TEXT)
        assert {(f.kind, f.value, f.method) for f in out} == {("actual", 2.9, "llm"), ("consensus", 3.0, "llm")}
        assert all(f.confidence == 0.5 for f in out)

    def test_json_inside_fences_is_read(self):
        raw = "```json\n" + json.dumps({"facts": [good_fact()]}) + "\n```"
        assert [f.value for f in mf.validate_llm_output(raw, TEXT)] == [2.9]

    def test_kind_must_fit_the_quote(self):
        text = "Economists at Barclays expect the Bank of England to cut Bank Rate to 3.75% in February."
        f = {"zone": "GBP", "geo": "UK", "indicator": "policy_rate", "kind": "decision", "value": 3.75,
             "unit": "%", "period": "2027-02", "quote": text}
        assert mf.validate_llm_output({"facts": [f]}, text) == []
        f.update(kind="forecast", institution="Barclays")
        assert [x.kind for x in mf.validate_llm_output({"facts": [f]}, text)] == ["forecast"]

    @pytest.mark.parametrize("raw", ["not json", "[1, 2]", '{"facts": "x"}', '{"facts": [{"zone": "JPY"}]}', "", "null",
                                     '{"facts": [{"zone": "USD", "geo": "US", "indicator": "sp500_fall", "kind": "actual", '
                                     '"value": -1.1, "unit": "%", "period": "2026-10", "quote": "The S&P 500 fell 1.1%."}]}'])
    def test_malformed_payload_gives_nothing(self, raw):
        assert mf.validate_llm_output(raw, TEXT) == []

    @pytest.mark.parametrize("bad", [
        {"value": 3.1},                                    # golden rule: not in quote
        {"quote": "core CPI rose to 2.9 percent"},         # not an exact excerpt
        {"indicator": "cpi_core"},                         # not in the closed list
        {"kind": "estimate"},                              # not a kind
        {"zone": "BRL"},                                   # not a zone
        {"zone": "USD"},                                   # geo JP-TKY is JPY
        {"unit": "bp"},                                    # unit of core_cpi_yoy is %
        {"period": "November"},                            # bad period format
        {"kind": "forecast"},                              # forecast without institution
        {"quote": "x" * 201},                              # > 200 chars
    ])
    def test_invalid_fact_is_dropped_others_kept(self, bad):
        out = mf.validate_llm_output({"facts": [good_fact(**bad), good_fact(kind="previous", value=2.6, quote="Prior 2.6%")]}, TEXT)
        assert [(f.kind, f.value) for f in out] == [("previous", 2.6)]

    def test_extract_llm_with_fake_client(self):
        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": json.dumps({"facts": [good_fact()]})}}

        class Client:
            def __init__(self):
                self.payload = None

            def post(self, url, json=None):
                self.payload = json
                return Resp()

        c = Client()
        out = mf.extract_llm(paper("Tokyo data", TEXT), client=c)
        assert [(f.indicator, f.value) for f in out] == [("core_cpi_yoy", 2.9)]
        assert "format" not in c.payload and c.payload["options"]["temperature"] == 0.0

    def test_extract_llm_network_error(self):
        class Boom:
            def post(self, *a, **k):
                raise ConnectionError("down")
        assert mf.extract_llm(paper("x", TEXT), client=Boom()) == []


# ---------------------------------------------------------------------------
# periods, events, storage, board
# ---------------------------------------------------------------------------
def test_period_end():
    assert mf.period_end("2026") == "2026-12-31"
    assert mf.period_end("2026-Q4") == "2026-12-31"
    assert mf.period_end("2027-Q1") == "2027-03-31"
    assert mf.period_end("2026-02") == "2026-02-28"
    assert mf.period_end("") is None


def test_ff_indicator_mapping():
    assert mf.ff_indicator("Core PCE Price Index m/m", "USD") == ("core_pce_mom", "US")
    assert mf.ff_indicator("Spanish Flash CPI y/y", "EUR") == ("cpi_yoy", "ES")
    assert mf.ff_indicator("Tokyo Core CPI y/y", "JPY") == ("core_cpi_yoy", "JP-TKY")
    assert mf.ff_indicator("Unemployment Claims", "USD") == ("jobless_claims", "US")
    assert mf.ff_indicator("Official Bank Rate", "GBP") == ("policy_rate", "UK")
    assert mf.ff_indicator("Advance GDP q/q", "USD") == ("gdp_qoq_saar", "US")
    assert mf.ff_indicator("Bank Holiday", "GBP") == (None, None)


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.models import Base
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def test_event_link_and_board(db):
    from backend.models import Paper
    now = utcnow()
    day = now.date().isoformat()
    p1 = Paper(id="a1", title="Spain November preliminary CPI +4.1% vs +4.6% y/y expected", abstract="Prior +4.9%",
               source="ForexLive", published_date=now, clean_title_hash="a1")
    p2 = Paper(id="a2", title="Spain’s inflation drops to 4.1% in November, led by fuel prices", abstract="",
               source="Investing.com", published_date=now - timedelta(hours=2), clean_title_hash="a2")
    p3 = Paper(id="a3", title="Euro outlook – Commerzbank", abstract="The bank expects the ECB deposit rate to reach "
               "3.00% in Q1 2027.", source="FXStreet Macro", published_date=now, clean_title_hash="a3")
    p4 = Paper(id="a4", title="Euro view – ING", abstract="ING economists expect the ECB deposit rate to reach "
               "2.5% in Q1 2027.", source="FXStreet Macro", published_date=now, clean_title_hash="a4")
    db.add_all([p1, p2, p3, p4])
    db.commit()
    ev = mf.event_index([{"title": "Spanish Flash CPI y/y", "country": "EUR",
                          "date": f"{day}T03:00:00-05:00"}])
    for p in (p1, p2, p3, p4):
        mf.store_facts(db, p, mf.extract_rules(p), ev)
    db.commit()
    facts = mf.query_facts(db, zone="EUR", indicator="cpi_yoy", kind="actual", days=2)
    assert facts and all(f["event_id"] == f"ff:{day}:EUR:spanish-flash-cpi-y-y" for f in facts)
    board = mf.build_board(db, days=2)
    row = next(r for r in board["rows"] if r["geo"] == "ES" and r["indicator"] == "cpi_yoy")
    assert row["last_actual"]["value"] == 4.1 and row["last_actual"]["confirmations"] == 2
    assert row["consensus"]["value"] == 4.6 and row["surprise"] == pytest.approx(-0.5)
    assert row["last_actual"]["quote"]
    pr = next(r for r in board["rows"] if r["geo"] == "EA" and r["indicator"] == "policy_rate")
    fc = pr["forecasts"][0]
    assert fc["period"] == "2027-Q1" and fc["count"] == 2 and fc["median"] == pytest.approx(2.75)
    assert fc["dispersion"] == pytest.approx(0.5) and fc["institutions"] == ["Commerzbank", "ING"]
    assert "median" in board["method"]["forecasts"] and board["coverage"]["EUR"]["inflation"]


def test_extract_recent_marks_papers(db, monkeypatch):
    from backend.models import MacroFact, Paper
    monkeypatch.setattr(mf, "_load_event_index", lambda: {})
    db.add(Paper(id="b1", title="UK inflation rose to 3.6% in October", abstract="", source="CNBC Economy",
                 published_date=utcnow(), clean_title_hash="b1"))
    db.add(Paper(id="b2", title="Nothing numeric here", abstract="", source="CNBC Economy",
                 published_date=utcnow(), clean_title_hash="b2"))
    db.commit()
    st = mf.extract_recent(db, days=3, use_llm=False)
    assert st["papers"] == 2 and st["facts"] == 1
    assert mf.extract_recent(db, days=3, use_llm=False)["papers"] == 0           # already done
    assert db.query(MacroFact).one().quote == "UK inflation rose to 3.6% in October"
    assert db.query(Paper).filter(Paper.macro_facts_at.isnot(None)).count() == 2


def test_api_routes(db, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    try:
        from backend import main
    except Exception as e:                                # embedding_prop / ollama absent
        pytest.skip(f"backend.main not importable here: {e}")
    from backend.models import Paper
    db.add(Paper(id="c1", title="UK inflation rose to 3.6% in October", abstract="", source="CNBC Economy",
                 published_date=utcnow() - timedelta(hours=1), clean_title_hash="c1"))
    db.commit()
    monkeypatch.setattr(mf, "_load_event_index", lambda: {})
    mf.extract_recent(db, days=2, use_llm=False)
    main.app.dependency_overrides[main.get_db] = lambda: db
    try:
        c = TestClient(main.app)
        r = c.get("/api/macro/facts", params={"zone": "GBP", "days": 2})
        assert r.status_code == 200 and r.json()["count"] == 1
        assert r.json()["facts"][0]["quote"] == "UK inflation rose to 3.6% in October"
        assert c.get("/api/macro/facts", params={"indicator": "nope"}).status_code == 422
        b = c.get("/api/macro/board", params={"days": 2}).json()
        assert b["rows"][0]["last_actual"]["value"] == 3.6 and "method" in b
        assert c.get("/mcp/macro_board", params={"days": 2}).status_code == 200
        assert c.get("/mcp/macro_facts", params={"days": 2}).json()["count"] == 1
        caps = c.get("/.well-known/mcp-capabilities").json()
        assert "macro_facts" in caps and "macro_board" in caps
    finally:
        main.app.dependency_overrides.clear()


def test_datetime_type():
    assert isinstance(paper("x").published_date, datetime)
