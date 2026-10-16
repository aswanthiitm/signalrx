import re

import pytest

from conftest import BUNDLES
from signalrx.agent.orchestrator import run_replay
from signalrx.render.dashboard import render_html


@pytest.fixture(scope="module")
def runs():
    return {b: run_replay(BUNDLES / f"{b}.yaml") for b in ("drreddy", "cipla", "maxhealth")}


def test_archetypes_from_data(runs):
    assert runs["drreddy"].profile.archetype == "formulations"
    assert runs["cipla"].profile.archetype == "formulations"
    assert runs["maxhealth"].profile.archetype == "hospital"


def test_primary_angles(runs):
    assert runs["drreddy"].discoveries[0]["angle"] == "replacement_concentration"
    assert runs["cipla"].discoveries[0]["angle"] == "counterparty_regulatory"
    assert runs["maxhealth"].discoveries[0]["angle"] == "payor_policy"
    assert runs["maxhealth"].discoveries[1]["angle"] == "capacity_gap"


def test_all_discoveries_verify(runs):
    for r in runs.values():
        for d in r.discoveries:
            v = d["verification"]
            assert v["passed"], (d["discovery"], v)
            assert len(v["families_for_discovery"]) >= 2


def test_required_elements_present(runs):
    for r in runs.values():
        d = r.discoveries[0]
        assert d["discovery"] and d["discovery"].count(". ") <= 1        # one sentence
        assert 3 <= len(d["why_it_matters"]) <= 5
        assert len({it["family"] for it in d["evidence_trail"]}) >= 3     # multiple independent sources
        assert all(it["url"].startswith("http") for it in d["evidence_trail"])
        assert d["timeline"] and d["numbers"]
        assert d["counterargument"]["text"]
        assert d["falsification"]
        assert d["strength"]["grade"] in ("Strong", "Moderate", "Weak") and d["strength"]["reasons"]


def test_key_numbers(runs):
    dr = runs["drreddy"].discoveries[0]
    assert "27.4%" in dr["discovery"] and "23.5%" in dr["discovery"]
    ci = runs["cipla"].discoveries[0]
    assert "14 days" in ci["discovery"] and "$233M" in ci["discovery"]
    mx = runs["maxhealth"].discoveries[0]
    assert "26% to 22%" in mx["discovery"]


def test_verifier_caught_misreported_growth(runs):
    led = runs["drreddy"].ledger
    dropped = [s.detail for s in led.trace if s.action == "growth-recompute:drop"]
    assert any("Investing.com" in d for d in dropped)


def test_skeptic_ran_and_found_weaknesses(runs):
    for r in runs.values():
        checks = r.discoveries[0]["checks"]
        assert len(checks) >= 4
        assert any(c["verdict"] == "weakens" for c in checks)


def test_counterparty_timing_reasoning(runs):
    other = next(c for c in runs["cipla"].discoveries[0]["checks"] if c["name"] == "other_driver")
    assert "after the Q3FY26 quarter closed" in other["detail"]


def test_dashboard_renders_all_sections(runs):
    for r in runs.values():
        html = render_html(r)
        for needle in ("The discovery", "Why it matters", "Evidence trail", "Timeline", "Calculations",
                       "Strongest counterargument", "Falsification conditions", "Evidence strength"):
            assert needle in html
        ids = set(re.findall(r'href="#ev-([A-Z0-9]+)"', html))
        anchors = set(re.findall(r'id="ev-([A-Z0-9]+)"', html))
        assert ids <= anchors, ids - anchors          # every citation chip resolves
