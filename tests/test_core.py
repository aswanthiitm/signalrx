import pytest

from signalrx import periods
from signalrx.analytics import metrics as M
from signalrx.ledger import EvidenceLedger
from signalrx.models import Evidence, Observation, Source
from signalrx.agent import verifier


def test_periods():
    assert periods.parse("Q1FY27") == ("Q", 2027, 1)
    assert periods.end_date("Q3FY26").isoformat() == "2025-12-31"
    assert periods.end_date("FY28").isoformat() == "2028-03-31"
    assert periods.prior_year("Q1FY27") == "Q1FY26"
    assert periods.next_quarter("Q4FY26") == "Q1FY27"
    assert periods.sort_key("FY26") > periods.sort_key("Q4FY26")


def test_metrics():
    assert M.growth(110, 100) == pytest.approx(10)
    assert M.implied_prior(55501, 55) == pytest.approx(35807, rel=1e-4)
    assert M.required_run_rate(9000, 5379, 21) == pytest.approx(2069.1, rel=1e-3)
    assert sum(M.shares({"a": 1, "b": 3}).values()) == pytest.approx(100)
    assert M.hhi({"a": 1}) == pytest.approx(10000)


def _led():
    led = EvidenceLedger("Co")
    led.add_source(Source("S1", "https://filing", "10-K", "Co", "company_filing"))
    led.add_source(Source("S2", "https://blog", "post", "Blog", "blog"))
    led.add_evidence(Evidence("E1", "S1", "figure", "x"))
    led.add_evidence(Evidence("E2", "S2", "figure", "y"))
    return led


def test_conflict_prefers_primary_source():
    led = _led()
    led.observe(Observation("Co", "revenue_total", "FY26", 100.0, "INR_mn", "E2"))
    led.observe(Observation("Co", "revenue_total", "FY26", 120.0, "INR_mn", "E1"))
    led.resolve_conflicts()
    assert led.get("revenue_total", "FY26").value == 120.0
    assert len(led.conflicts) == 1


def test_growth_recompute_drops_wrong_sign():
    led = _led()
    led.observe(Observation("Co", "revenue_total", "Q1FY26", 85452, "INR_mn", "E1"))
    led.observe(Observation("Co", "revenue_total", "Q1FY27", 80705, "INR_mn", "E1"))
    led.observe(Observation("Co", "revenue_growth_yoy", "Q1FY27", 6.0, "%", "E2", {"basis": "reported"}))
    notes = verifier.recompute_growth(led)
    assert notes and "-5.6%" in notes[0]
    assert led.get("revenue_growth_yoy", "Q1FY27", basis="reported") is None
    assert led.evidence["E2"].verified is False


def test_unknown_evidence_rejected():
    led = _led()
    with pytest.raises(KeyError):
        led.observe(Observation("Co", "x", "FY26", 1, "%", "E99"))
