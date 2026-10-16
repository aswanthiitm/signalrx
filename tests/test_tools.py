from conftest import FIX
from signalrx.tools import sources as S
from signalrx.tools.documents import verify_quote


def test_screener_parse():
    d = S.parse_screener_tables((FIX / "screener_sample.html").read_text())
    q = d["quarters"]
    assert q["Sales"]["Q1FY27"] == 7119 and q["Sales"]["Q3FY26"] == 6500
    assert q["OPM %"]["Q4FY26"] == 18
    assert d["profit-loss"]["Sales"]["FY26"] == 27800 and "TTM" not in d["profit-loss"]["Sales"]
    assert d["warehouse_id"] == "456"
    kinds = {x["kind"] for x in d["documents"]}
    assert {"annual_report", "transcript", "presentation"} <= kinds


def test_period_mapping():
    assert S.screener_period("Jun 2026", "quarters") == "Q1FY27"
    assert S.screener_period("Mar 2026", "quarters") == "Q4FY26"
    assert S.screener_period("Mar 2025", "annual") == "FY25"
    assert S.screener_period("Dec 2025", "annual") is None


def test_rss_and_first_coverage():
    items = S.parse_rss((FIX / "news.xml").read_text())
    assert items[0]["date"] == "2026-01-14"
    hit = S.first_coverage(items, ["usfda", "supplier"])
    assert hit and hit["publisher"] == "Wire"


def test_index_universe_filters_healthcare():
    rows = S.parse_index_csv((FIX / "index.csv").read_text(), "nifty50")
    assert [r["symbol"] for r in rows] == ["ALPHA", "GAMMA"]


def test_openfda_parsers():
    rec = S.parse_openfda_recalls({"results": [{"recall_initiation_date": "20260313", "classification": "Class II",
                                                "product_description": "Lanreotide injection", "reason_for_recall": "Lack of sterility assurance",
                                                "recalling_firm": "X USA", "status": "Ongoing"}]})
    assert rec[0]["date"] == "2026-03-13"
    ap = S.parse_openfda_approvals({"results": [{"application_number": "ANDA1", "products": [{"brand_name": "A"}],
                                                 "submissions": [{"submission_type": "ORIG", "submission_status": "AP",
                                                                  "submission_status_date": "20250102"}]}]})
    assert S.approvals_by_year(ap) == {2025: 1}


def test_nse_parser():
    rows = S.parse_nse_announcements([{"an_dt": "14-Jan-2026 18:01:02", "desc": "Updates", "attchmntText": "Lanreotide supply",
                                       "attchmntFile": "https://nsearchives/x.pdf", "symbol": "X"}])
    assert rows[0]["date"] == "2026-01-14"


def test_quote_grounding():
    doc = "The company said that   certain batches were found to be out of specification due to an issue associated with the API."
    assert verify_quote("certain batches were found to be out of specification", doc)[0]
    assert verify_quote("certain batches were found to be out of specfication", doc)[0]      # typo tolerance
    assert not verify_quote("the company recalled all batches from the US market", doc)[0]   # invention rejected
