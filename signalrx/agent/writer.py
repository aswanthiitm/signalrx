"""Assemble the eight required output elements from a tested hypothesis.

Offline mode writes from per-angle templates over verified facts. LLM mode
(llm_rewrite) lets the model improve the prose, under the rule that it may only
use numbers present in the facts and must cite evidence ids; the verifier then
re-checks every number, and any unmatched figure fails the run.
"""
from __future__ import annotations

from .. import fmt, periods
from ..ledger import EvidenceLedger
from ..models import Hypothesis

BRIEF = {"D1": "Has its business model quietly changed, and when?",
         "D2": "Has a regulatory or compliance development hit its financials before mainstream coverage?",
         "D3": "How has its geographic revenue exposure shifted over five years, and what drove it?",
         "X": "Additional angle chosen by the agent"}

ANGLE_BRIEF = {"geo_mix_shift": "D3", "replacement_concentration": "D1", "counterparty_regulatory": "D2",
               "own_site_regulatory": "D2", "specialty_pivot": "D1", "payor_policy": "D2", "capacity_gap": "X"}


def _check(h: Hypothesis, name: str):
    return next((c for c in h.checks if c.name == name), None)


def _m(v, unit):
    return fmt.money(v, unit)


def _times(n: int) -> str:
    return {1: "once", 2: "twice", 3: "three times", 4: "four times"}.get(n, f"{n} times")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


# ------------------------------------------------------------ per angle

def _replacement(led: EvidenceLedger, h: Hypothesis) -> dict:
    f = h.facts
    s = f["series"]
    ps = list(s)
    first_p, peak_p = ps[0], f["peak_period"]
    q = f.get("q_series") or {}
    lq_p, lq = (list(q)[-1], list(q.values())[-1]) if q else (ps[-1], s[ps[-1]])
    old = ", ".join(f["old_products"]) or "its main product"
    prod = f["product"]
    n = len(f["incidents"])
    gc = f.get("guidance_cut")
    prov = f.get("provision")
    below = lq < s[first_p]
    disc = (f"{led.company} has already stopped being a {f['segment']}-led business — {f['segment']} fell from a {s[peak_p]:.1f}% "
            f"peak ({peak_p}) to {lq:.1f}% of revenue in {lq_p}" + (f", below even its {first_p} share of {s[first_p]:.1f}%" if below else "")
            + (f", while the rest of the business grew {f['ex_growth']:.1f}% in {f['latest_period']}" if "ex_growth" in f else "")
            + f" — which makes {prod}, not {old}, the swing factor, and {prod} has slipped {_times(n)} in {f['months_between_incidents']} months"
            + (f", ending in a {_m(prov['value'], prov['unit'])} write-down" if prov else "")
            + (f" ({f['provision_pct_profit']:.0f}% of {prov['period']} net profit)" if prov and "provision_pct_profit" in f else "")
            + (f" and a ~{abs(gc['pct']):.0f}% cut to the {gc['period']} plan ({gc['before']:g} → {gc['label']} {gc['unit']})" if gc else "")
            + ".")
    breadth = _check(h, "breadth")
    inorg = _check(h, "inorganic")
    why = [
        f"The {old} cliff is already in the numbers: at {lq:.1f}% of revenue, {f['segment']} is {'below' if below else 'near'} its "
        f"{first_p} level, so the debate should move from 'how big is the cliff' to 'what replaces it'.",
        (breadth.detail.split(" So ")[0] if breadth else f"Growth outside {f['segment']} is the replacement.")
        + (f" Part of it was acquired ({f['acquisitions'][0].split(';')[0]})." if f.get("acquisitions") else ""),
        f"{_cap(prod)} is the largest new organic lever, and its setbacks came from different failure points: "
        + "; ".join(f"{fmt.nice_date(x['date'], x['precision'])} ({x['actor']}): {x['title'].split(';')[0]}" for x in f["incidents"][:3]) + ".",
    ]
    if f.get("partners"):
        p = f["partners"][0]
        why.append(f"Its supply chain runs through an external partner ({p['name']}, {p['note']}): a dependency that shows up in "
                   "partner and press disclosures, not in the segment table.")
    if prov and "provision_pct_profit" in f:
        why.append(f"Sensitivity: the {prov['period']} provision ({_m(prov['value'], prov['unit'])}) equals {f['provision_pct_profit']:.0f}% of "
                   f"that quarter's net profit" + (f"; the plan cut removes about {gc['before'] - gc['after']:g} {gc['unit']} of supply (midpoint)." if gc else "."))
    fals = []
    temp = _check(h, "temporary")
    fut = next((led.events[i] for i in h.events if "future" in led.events[i].tags), None) or next(
        (ev for ev in led.events if "future" in ev.tags and (ev.subject == prod or prod in ev.tags)), None)
    if fut:
        fals.append({"condition": f"Commercial {prod} supply resumes by {fmt.nice_date(fut.date, fut.date_precision)} as guided"
                     + (f" and {gc['label']} {gc['unit']} are delivered in {gc['period']}" if gc else "")
                     + "; that would show the setbacks were a one-time scale-up problem, not structural fragility.",
                     "monitor": f"exchange filings / quarterly results mentioning {prod}", "evidence": fut.evidence})
    keys = [{"label": f"{f['segment']} share of revenue", "value": f"{lq:.1f}%", "sub": f"{lq_p}; peak {s[peak_p]:.1f}% in {peak_p}"}]
    if "ex_growth" in f:
        keys.append({"label": f"Growth ex-{f['segment']}", "value": f"{f['ex_growth']:+.1f}%", "sub": f"{f['latest_period']} vs {f['segment']} {f.get('seg_growth', 0):+.1f}%"})
    if prov:
        keys.append({"label": f"{_cap(prod)} write-down", "value": _m(prov['value'], prov['unit']),
                     "sub": f"{f.get('provision_pct_profit', 0):.0f}% of {prov['period']} net profit"})
    if gc:
        keys.append({"label": f"{gc['period']} {_cap(prod)} plan", "value": f"{gc['before']:g} → {gc['label']}", "sub": f"{gc['unit']} (~{abs(gc['pct']):.0f}% cut, midpoint)"})
    fals.append({"condition": f"No further regulatory or quality incident on {prod} over the next two quarters.",
                 "monitor": "regulator databases and Reg-30 filings", "evidence": []})
    fals.append({"condition": f"{f['segment']} share rises for two consecutive quarters on new launches (not one-offs), which would "
                 f"make {f['segment']} — not {prod} — the swing factor again.", "monitor": "quarterly segment table", "evidence": []})
    return {"discovery": disc, "why": why, "falsification": fals, "keys": keys}


def _counterparty(led: EvidenceLedger, h: Hypothesis) -> dict:
    f = h.facts
    st = f.get("step", {})
    unit = st.get("unit", "USD_mn")
    reg0 = f["regulator_events"][0]
    share = f.get("product_share")
    lo, hi = f.get("post_step_range", [None, None])
    disc = (f"{led.company}'s sharpest {f['segment']} decline came from a plant it does not own: {f['partner']}, the exclusive maker of "
            f"{f['product']}" + (f" (~{share['value']:.0f}% of US sales in {share['period']}{', ' + share['note'] if share.get('note') else ''})" if share else "")
            + f", closed an FDA inspection with observations on {fmt.nice_date(reg0['date'])}; {f['segment']} revenue then fell "
            f"{abs(st['pct']):.0f}% quarter-on-quarter in {st['period']} ({_m(st['from'], unit)} → {_m(st['to'], unit)}), a quarter that ended "
            f"{f['quarter_end_before_disclosure_days']} days before the company disclosed the problem, and has stayed at "
            f"{_m(lo, unit)}–{_m(hi, unit)} a quarter since"
            + (f" — with FY27 guidance now assuming zero {f['product']}, the {_m(f['target']['value'], f['target']['unit'])} exit run-rate "
               f"target needs {f['target']['uplift_pct']:.0f}% more than the latest quarter" if f.get("target") else "") + ".")
    od = _check(h, "other_driver")
    why = [
        "Third-party plant risk sits outside the company's own site list and FDA status: it appears first in the partner's inspection "
        "record, which is why a dashboard built on the company's filings misses it.",
        f"Timing: the inspection closed {fmt.nice_date(reg0['date'])}; the hit landed inside the {st['period']} quarter; the company disclosed on "
        f"{fmt.nice_date(f['disclosure']['date'])} ({f['lag_regulator_to_disclosure_days']} days after the inspection)"
        + (f" and mainstream coverage followed on {fmt.nice_date(f['media_first']['date'])}." if f.get("media_first") else "."),
    ]
    if "explained_pct" in f:
        why.append(f"Sizing: a full loss of {f['product']} (~{_m(f['product_run_rate'], unit)} a quarter) explains about "
                   f"{f['explained_pct']:.0f}% of the {st['period']} step-down"
                   + ("; a second driver accounts for the rest (see counterargument)." if od and od.verdict == "weakens" else "."))
    steps = [e for e in f["regulator_events"]]
    if len(steps) > 1:
        why.append("Escalation went the wrong way after the inspection: " + " → ".join(
            f"{fmt.nice_date(e['date'])}: {e['title'].split('(')[0].strip()}" for e in steps) + ".")
    if f.get("guidance_claims"):
        why.append(f"Management has stopped counting on a fix: \"{f['guidance_claims'][0]}\" — recovery now depends entirely on new launches.")
    keys = [{"label": f"{f['segment']} quarterly revenue", "value": f"{_m(st['from'], unit)} → {_m(st['to'], unit)}",
             "sub": f"{st['pct']:+.0f}% QoQ in {st['period']}"},
            {"label": "Inspection → disclosure", "value": f"{f['lag_regulator_to_disclosure_days']} days",
             "sub": f"quarter closed {f['quarter_end_before_disclosure_days']} days before disclosure"}]
    if "explained_pct" in f:
        keys.append({"label": f"Step-down explained by {f['product']}", "value": f"~{f['explained_pct']:.0f}%", "sub": "if fully lost; rest is other drivers"})
    if f.get("target"):
        keys.append({"label": "Uplift needed to target", "value": f"{f['target']['uplift_pct']:+.0f}%",
                     "sub": f"{_m(f['target']['value'], f['target']['unit'])} exit run-rate"})
    fals = [
        {"condition": f"{f['partner']}'s site is cleared (OAI lifted / re-inspection without observations) or an alternate site is approved, "
                      f"and {f['product']} supply restarts.", "monitor": "FDA Data Dashboard classification; company Reg-30 filings", "evidence": []},
        {"condition": f"{f['segment']} quarterly revenue returns above {_m(st['from'], unit)} (the pre-step level) within FY27 without "
                      f"{f['product']}, which would show the business has outgrown the dependency.", "monitor": "quarterly results", "evidence": []},
        {"condition": f"Product-level data showing {f['product']} still shipped normally during {st['period']}, which would move the "
                      "step-down to the other driver and break the causal link.", "monitor": "earnings-call disclosures, IQVIA-type data cited in press",
         "evidence": []},
    ]
    return {"discovery": disc, "why": why, "falsification": fals, "keys": keys}


def _payor(led: EvidenceLedger, h: Hypothesis) -> dict:
    f = h.facts
    pols = f["policies"]
    act = f["action"]
    ser = f.get("arpob_series", {})
    ser_txt = ", ".join(f"{v:+.1f}% in {p}" for p, v in ser.items() if v < 3) if ser else ""
    ben = f.get("benefit")
    pol_txt = " and ".join(f"{p.get('short') or p['title'].split(':')[0]} ({fmt.nice_date(p['date'], p['precision'])})" for p in pols[:2])
    disc = (f"{led.company}'s ARPOB stall ({ser_txt}) was a payor-policy shock, not weak demand: after the {pol_txt}, it "
            f"{act['title'][0].lower() + act['title'][1:]} from {fmt.nice_date(act['date'], act['precision'])}, cutting {f['specialty']} from "
            f"{f['share']['from']:.0f}% to {f['share']['to']:.0f}% of inpatient revenue — so reported growth ({f['reported']:.0f}%) understates "
            f"the rest of the business ({f['adjusted']:.0f}%)"
            + (f", and the {f['gap_period']} ARPOB rebound (+{f['arpob_reported_growth']:.0f}%) owes about {ben['pct_of_q_revenue']:.1f}pp to the "
               f"{ben['policy']}" if ben and "arpob_reported_growth" in f else "") + ".")
    sw = _check(h, "sector_wide")
    why = [
        f"The drag is policy-made, so it reverses on policy, not on demand: management calls it structural unless the payor's terms improve.",
        f"Headline growth understates core growth by {f['adjusted'] - f['reported']:.0f}pp. Cross-check: the share shift alone implies "
        f"{f['specialty']} revenue growth of {f['implied_spec_growth']:.1f}% and non-{f['specialty']} growth of {f['implied_rest_growth']:.1f}%, "
        f"close to the reported {f['adjusted']:.0f}%.",
    ]
    if ben and "arpob_core_growth" in f:
        why.append(f"ARPOB optics: of the +{f['arpob_reported_growth']:.0f}% in {f['gap_period']}, about {ben['pct_of_q_revenue']:.1f}pp is the "
                   f"tariff revision (~{_m(ben['annual'], ben['unit'])} a year), leaving core ARPOB growth near {f['arpob_core_growth']:.1f}%.")
    payors = [ln for ln in led.links if ln.type == "payor"]
    if sw and sw.verdict == "survives":
        why.append(sw.detail + (f" The exposure comes from the payor relationship on record: {payors[0].b} ({payors[0].note})."
                                if payors else ""))
    quant = [ev for ev in led.events if ev.kind == "results" and f["specialty"] in ev.tags]
    first_res = next((ev for ev in sorted(quant, key=lambda e: periods.parse_date(e.date))
                      if periods.parse_date(ev.date) > periods.parse_date(act["date"])), None)
    last_res = max(quant, key=lambda e: periods.parse_date(e.date)) if quant else None
    if first_res and last_res:
        lead_m = round(fmt.days(pols[0]["date"], act["date"]) / 30.4)
        f["policy_to_action_months"] = lead_m
        why.append(f"Timeline: the first payor rule came {lead_m:.0f} months before the company acted; the effect was visible in results from "
                   f"{fmt.nice_date(first_res.date, first_res.date_precision)} and quantified in {fmt.nice_date(last_res.date, last_res.date_precision)}.")
    fut = next((ev for ev in led.events if "future" in ev.tags and f["specialty"] in ev.tags), None)
    if fut:
        gap_m = round(fmt.days(act["date"], fut.date) / 30.4)
        f["action_to_normalisation_months"] = gap_m
        if 10 <= gap_m <= 14:
            why.insert(2, f"Read the 'normalisation' guidance carefully: it falls {gap_m} months after the change, i.e. when the base effect "
                          f"laps. Year-on-year growth normalises, but the lost {f['specialty']} revenue does not come back unless terms change.")
    fals = [
        {"condition": f"{f['specialty'].capitalize()} back to ≥{f['share']['from']:.0f}% of inpatient revenue"
                      + (f" by {fmt.nice_date(fut.date, fut.date_precision)} (management's own normalisation date)" if fut else "")
                      + " without a change in payor terms: that would point to case-mix, not policy.",
         "monitor": "quarterly investor presentation (specialty mix)", "evidence": fut.evidence if fut else []},
        {"condition": "The payor relaxes its terms (a new order or memorandum) and the specialty share recovers: confirms the mechanism "
                      "but ends the drag.", "monitor": f"{payors[0].b if payors else 'payor'} orders and memoranda", "evidence": []},
        {"condition": f"Ex-{f['specialty']} growth converges to reported growth (gap under one percentage point) for two quarters: the policy effect has "
                      "washed out of the base.", "monitor": "quarterly results", "evidence": []},
    ]
    keys = [{"label": f"{_cap(f['specialty'])} share of IP revenue", "value": f"{f['share']['from']:.0f}% → {f['share']['to']:.0f}%",
             "sub": " → ".join(f["share"]["periods"])},
            {"label": "Growth: reported vs ex-" + f["specialty"], "value": f"{f['reported']:.0f}% vs {f['adjusted']:.0f}%", "sub": f["gap_period"]}]
    if ben and "arpob_core_growth" in f:
        keys.append({"label": f"ARPOB growth {f['gap_period']}", "value": f"+{f['arpob_reported_growth']:.0f}%",
                     "sub": f"~{ben['pct_of_q_revenue']:.1f}pp from tariff revision; core ~{f['arpob_core_growth']:.1f}%"})
    if "policy_to_action_months" in f:
        keys.append({"label": "Payor rule → company action", "value": f"{f['policy_to_action_months']} months", "sub": "effect quantified two quarters later"})
    return {"discovery": disc, "why": why, "falsification": fals, "keys": keys}


def _capacity(led: EvidenceLedger, h: Hypothesis) -> dict:
    f = h.facts
    f["gap_low"], f["gap_high"] = f["target_low"] - f["current"], f["target_high"] - f["current"]
    f["nine_month_path"] = round(f["current"] + f["need_low"] * 0.75)
    disc = (f"{led.company}'s bed plan is running far ahead of its builds: operational beds rose {f['trailing']:,.0f} in the year to "
            f"{f['current_period']} (to {f['current']:,.0f}), but the {f['target_low']:,.0f}–{f['target_high']:,.0f} target for {f['by']} needs "
            f"{f['need_low']:,.0f}–{f['need_high']:,.0f} a year, {f['accel_low']:.1f}–{f['accel_high']:.1f}x the current pace"
            + (f", and the target has been restated {len(f['target_history'])} times" if len(f["target_history"]) > 1 else "") + ".")
    why = [
        "Hospital earnings follow commissioned beds, not announced ones; consensus built on the headline target overstates the near-term run-rate.",
        f"The gap leaves {f['months_left']:.0f} months to add {f['gap_low']:,.0f}–{f['gap_high']:,.0f} beds.",
        "Dated delays (clearances) are on record, so part of the gap is execution, not just phasing."
        if f["delays"] else "Project schedules are phased; the gap is back-ended.",
        "New beds start at low occupancy and carry pre-commissioning costs, so an accelerated build would dilute margins first.",
    ]
    fals = [{"condition": f"Operational beds reach {f['nine_month_path']:,.0f} within 9 months (the run-rate the low target requires).",
             "monitor": "quarterly operational bed count", "evidence": []},
            {"condition": "Management formally phases the target out beyond the stated year (which resolves the gap by moving the goalpost).",
             "monitor": "investor presentations", "evidence": []}]
    keys = [{"label": "Operational beds", "value": f"{f['current']:,.0f}", "sub": f"+{f['trailing']:,.0f} in 12 months to {f['current_period']}"},
            {"label": f"Needed per year to {f['by']}", "value": f"{f['need_low']:,.0f}–{f['need_high']:,.0f}", "sub": f"for {f['target_low']:,.0f}–{f['target_high']:,.0f} beds"},
            {"label": "Acceleration required", "value": f"{f['accel_low']:.1f}–{f['accel_high']:.1f}x", "sub": "vs trailing 12-month pace"}]
    return {"discovery": disc, "why": why, "falsification": fals, "keys": keys}


def _geo(led: EvidenceLedger, h: Hypothesis) -> dict:
    f = h.facts
    disc = h.statement
    why = ["Geographic concentration drives sensitivity to US price erosion and product cliffs.",
           f"Growth outside {f['segment']} was {f.get('ex_growth', 0):.1f}% in {f.get('latest_period', '')}."]
    fals = [{"condition": f"{f['segment']} share rises again for two consecutive quarters.", "monitor": "segment table", "evidence": []}]
    s = f["series"]
    keys = [{"label": f"{f['segment']} share", "value": f"{s[f['latest_period']]:.1f}%", "sub": f"{f['latest_period']}; peak {s[f['peak_period']]:.1f}%"}]
    return {"discovery": disc, "why": why, "falsification": fals, "keys": keys}


def _open(led: EvidenceLedger, h: Hypothesis) -> dict:
    f = h.facts
    return {"discovery": h.statement, "why": f.get("why", [])[:5],
            "falsification": [{"condition": c, "monitor": "see evidence trail", "evidence": []} for c in f.get("falsification", [])],
            "keys": []}


WRITERS = {"open": _open, "replacement_concentration": _replacement, "counterparty_regulatory": _counterparty,
           "payor_policy": _payor, "capacity_gap": _capacity, "geo_mix_shift": _geo}


# ---------------------------------------------------------------- common

def _trail(led: EvidenceLedger, h: Hypothesis) -> list[dict]:
    order = {"company_filing": 0, "exchange_filing": 0, "transcript": 1, "investor_presentation": 1, "regulator": 2,
             "counterparty": 2, "rating_agency": 3, "broker": 3, "media": 4, "aggregator": 5, "blog": 6}
    items = []
    for e in h.support:
        ev = led.evidence[e]
        s = led.sources[ev.source]
        items.append({"evidence": [e], "text": ev.text, "source": s.title, "publisher": s.publisher, "type": s.type,
                      "family": s.family, "date": s.published, "url": s.url, "stance": "supports",
                      "verified": ev.verified, "note": ev.note})
    for e in h.contra:
        if e in h.support or e not in led.evidence:
            continue
        ev = led.evidence[e]
        s = led.sources[ev.source]
        items.append({"evidence": [e], "text": ev.text, "source": s.title, "publisher": s.publisher, "type": s.type,
                      "family": s.family, "date": s.published, "url": s.url, "stance": "opposes",
                      "verified": ev.verified, "note": ev.note})
    items.sort(key=lambda x: (x["stance"] != "supports", order.get(x["type"], 9), periods.parse_date(x["date"] or "2000").toordinal()))
    return items


def _timeline(led: EvidenceLedger, h: Hypothesis) -> list[dict]:
    idx = set(h.events)
    tags = {t.lower() for t in h.facts.get("core_terms", [])} | {str(h.facts.get(k, "")).lower() for k in ("segment", "product", "specialty", "partner")}
    for i, ev in enumerate(led.events):
        if {t.lower() for t in ev.tags} & tags or (ev.subject or "").lower() in tags:
            idx.add(i)
    rows = []
    for i in sorted(idx, key=lambda i: periods.parse_date(led.events[i].date)):
        ev = led.events[i]
        rows.append({"date": ev.date, "label": fmt.nice_date(ev.date, ev.date_precision), "kind": ev.kind, "actor": ev.actor,
                     "title": ev.title, "evidence": ev.evidence, "future": "future" in ev.tags,
                     "in_hypothesis": i in h.events})
    return rows


def _numbers(led: EvidenceLedger, h: Hypothesis) -> list[dict]:
    rows = []
    seen = set()
    for cid in h.calcs:
        c = led.calcs.get(cid)
        if not c or c.name in seen:
            continue
        seen.add(c.name)
        rows.append({"id": c.id, "name": c.name, "value": c.value, "unit": c.unit, "formula": c.formula, "inputs": c.inputs,
                     "evidence": c.evidence})
    return rows


def _counterargument(led: EvidenceLedger, h: Hypothesis) -> dict:
    priority = ["refutes", "weakens"]
    order = ["breadth", "other_driver", "temporary", "low_margin", "sector_wide", "inorganic", "new_capacity", "phasing",
             "already_mainstream", "one_off"]
    cands = [c for c in h.checks if c.verdict in priority]
    cands.sort(key=lambda c: (priority.index(c.verdict), order.index(c.name) if c.name in order else 99))
    contra_claims = [c for c in led.claims if c.evidence in h.contra]
    if not cands:
        return {"text": "No alternative explanation weakened the finding.", "evidence": [], "check": None}
    top = cands[0]
    text = top.detail
    if len(cands) > 1:
        text += " Also: " + " ".join(c.detail.split(". ")[0] + "." for c in cands[1:3])
    if contra_claims:
        text += " Management's position: " + " ".join(f"\"{c.text}\"" for c in contra_claims[:2])
    return {"text": text, "evidence": sorted(set(top.evidence + [c.evidence for c in contra_claims])), "check": top.name}


def _context_items(led: EvidenceLedger, ids: set[str]) -> list[dict]:
    out = []
    for e in sorted(ids):
        if e not in led.evidence:
            continue
        ev = led.evidence[e]
        s = led.sources[ev.source]
        out.append({"evidence": [e], "text": ev.text, "source": s.title, "publisher": s.publisher, "type": s.type,
                    "family": s.family, "date": s.published, "url": s.url, "stance": "context",
                    "verified": ev.verified, "note": ev.note})
    return out


def write(led: EvidenceLedger, h: Hypothesis) -> dict:
    body = WRITERS[h.angle](led, h)
    st = h.facts["strength"]
    disc_ev = sorted(set(h.support))
    brief = ANGLE_BRIEF.get(h.angle, "X")
    trail = _trail(led, h)
    timeline = _timeline(led, h)
    numbers = _numbers(led, h)
    counter = _counterargument(led, h)
    cited = {e for t in timeline for e in t["evidence"]} | {e for c in h.checks for e in c.evidence} \
        | {e for n in numbers for e in n["evidence"]} | {e for f in body["falsification"] for e in f["evidence"]} | set(counter["evidence"])
    have = {it["evidence"][0] for it in trail}
    trail += _context_items(led, cited - have)
    return {
        "hypothesis": h.id, "angle": h.angle, "brief_question": brief, "brief_text": BRIEF[brief], "title": h.title,
        "discovery": body["discovery"], "discovery_evidence": disc_ev,
        "why_it_matters": body["why"][:5],
        "key_numbers": body.get("keys", []),
        "evidence_trail": trail,
        "timeline": timeline,
        "numbers": numbers,
        "counterargument": counter,
        "falsification": body["falsification"],
        "strength": {"grade": st["grade"], "score": st["score"], "reasons": st["reasons"]},
        "scores": h.scores, "novelty_why": h.facts.get("novelty_why", []),
        "checks": [c.__dict__ for c in h.checks],
    }
