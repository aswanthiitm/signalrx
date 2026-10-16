"""Hypothesis templates, one per angle in the knowledge pack.

Each template reads only the ledger and the scan's anomalies. It never sees a
company name in code; it finds the declining segment, the fading product, the
partner plant, the policy instrument and so on from the evidence itself, and
records every number it uses as a Calculation so the verifier can re-check it.

In LLM mode the investigator can also propose free-form hypotheses; those go
through the same skeptic / verifier / scorer path (see agent/llm_agent.py).
"""
from __future__ import annotations

import itertools

from .. import fmt, periods
from ..analytics import metrics as M
from ..analytics.scan import Anomaly
from ..ledger import EvidenceLedger
from ..models import Hypothesis, Observation

_ids = itertools.count(1)


def _hid() -> str:
    return f"H{next(_ids)}"


def reset_ids() -> None:
    global _ids
    _ids = itertools.count(1)


def _ev_of_calcs(led: EvidenceLedger, calc_ids: list[str]) -> list[str]:
    out: list[str] = []
    for c in calc_ids:
        out += led.calcs[c].evidence
    return out


def _events(led: EvidenceLedger, pred) -> list[int]:
    return [i for i, ev in sorted(enumerate(led.events), key=lambda t: periods.parse_date(t[1].date)) if pred(ev)]


def _annual_decliner(anoms: list[Anomaly]) -> tuple[Anomaly | None, list[Anomaly]]:
    shifts = [a for a in anoms if a.type == "mix_shift"]
    annual = [a for a in shifts if a.data["freq"] == "A"] or shifts
    neg = [a for a in annual if a.magnitude < 0]
    if not neg:
        return None, []
    dec = min(neg, key=lambda a: a.magnitude)
    gainers = [a for a in annual if a.magnitude > 0 and a.data["freq"] == dec.data["freq"]]
    return dec, sorted(gainers, key=lambda a: -a.magnitude)


def _quarter_share(anoms: list[Anomaly], seg: str) -> Anomaly | None:
    q = [a for a in anoms if a.type == "mix_shift" and a.data["freq"] == "Q" and a.subject == seg]
    return q[0] if q else None


# ----------------------------------------------------------------- geo mix

def geo_mix_shift(led: EvidenceLedger, anoms: list[Anomaly]) -> list[Hypothesis]:
    dec, gainers = _annual_decliner(anoms)
    if not dec:
        return []
    s = dec.data["series"]
    ps = list(s)
    peak = dec.data["peak_period"]
    qa = _quarter_share(anoms, dec.subject)
    calcs = list(dec.calcs) + (qa.calcs if qa else [])
    # Growth of everything except the decliner, latest year vs prior year.
    latest, prev = ps[-1], periods.prior_year(ps[-1])
    rest_now = [o for o in led.observations if o.entity == led.company and o.metric == "revenue_segment"
                and o.period == latest and o.dims.get("segment") != dec.subject and o.unit == dec.data["unit"]]
    rest_prev = [led.get("revenue_segment", prev, segment=o.dims["segment"]) for o in rest_now]
    facts: dict = {"segment": dec.subject, "series": s, "peak_period": peak, "first_period": ps[0],
                   "latest_period": latest, "unit": dec.data["unit"],
                   "gainers": [{"segment": g.subject, "from": list(g.data["series"].values())[0],
                                "to": list(g.data["series"].values())[-1]} for g in gainers]}
    if all(rest_prev):
        g = M.growth(sum(o.value for o in rest_now), sum(o.value for o in rest_prev))
        c = led.calc(f"Revenue growth excluding {dec.subject}, {latest}", "sum(other segments t) / sum(other segments t-1) - 1",
                     g, "%", rest_now + rest_prev)
        dprev = led.get("revenue_segment", prev, segment=dec.subject)
        dnow = led.get("revenue_segment", latest, segment=dec.subject)
        cd = led.calc(f"{dec.subject} revenue growth, {latest}", "t / t-1 - 1", M.growth(dnow.value, dprev.value), "%", [dnow, dprev])
        calcs += [c.id, cd.id]
        facts.update(ex_growth=g, ex_growth_calc=c.id, seg_growth=led.calcs[cd.id].value)
    if qa:
        facts.update(q_series=qa.data["series"])
    acq = _events(led, lambda ev: ev.kind == "acquisition" and any(g["segment"] in ev.tags for g in facts["gainers"]))
    facts["acquisitions"] = [led.events[i].title for i in acq]
    first, top, last = s[ps[0]], s[peak], s[latest]
    gtxt = ", ".join(f"{g['segment']} {g['from']:.0f}%→{g['to']:.0f}%" for g in facts["gainers"][:3])
    stmt = (f"{dec.subject} went from {first:.1f}% of segment revenue in {ps[0]} to {top:.1f}% in {peak} and "
            f"{last:.1f}% in {latest}" + (f" ({list(qa.data['series'].values())[-1]:.1f}% in {list(qa.data['series'])[-1]})" if qa else "")
            + (f"; {gtxt} took its place." if gtxt else "."))
    h = Hypothesis(_hid(), "geo_mix_shift", f"Revenue centre of gravity has moved away from {dec.subject}", stmt, facts,
                   support=sorted(set(_ev_of_calcs(led, calcs))), calcs=calcs, events=acq)
    h.scores["materiality"] = min(1.0, abs(top - last) / 25.0)
    h.facts["core_terms"] = [dec.subject.lower(), "share", "revenue"]
    return [h]


# ------------------------------------------------------ replacement engine

def replacement_concentration(led: EvidenceLedger, anoms: list[Anomaly]) -> list[Hypothesis]:
    dec, gainers = _annual_decliner(anoms)
    if not dec:
        return []
    seg = dec.subject
    old = {ev.subject for ev in led.events if ev.kind == "loe" and seg in ev.tags and ev.subject}
    products: dict[str, list[int]] = {}
    for i, ev in enumerate(led.events):
        if "incident" not in ev.tags or ev.kind in ("results", "media"):
            continue
        for p in {ev.subject, *ev.tags}:
            if p and p not in old and p not in (seg, "incident", "quality", "future") and any(
                    p == ln.product for ln in led.links) | any(p == g.subject for g in led.guidance) | (p == ev.subject):
                products.setdefault(p, []).append(i)
    if not products:
        return []
    prod, inc = max(products.items(), key=lambda kv: len(set(kv[1])))
    inc = sorted(set(inc), key=lambda i: periods.parse_date(led.events[i].date))
    if len(inc) < 2:
        return []
    calcs = list(dec.calcs)
    facts: dict = {"segment": seg, "old_products": sorted(old), "product": prod, "series": dec.data["series"],
                   "peak_period": dec.data["peak_period"], "unit": dec.data["unit"],
                   "incidents": [{"date": led.events[i].date, "precision": led.events[i].date_precision,
                                  "title": led.events[i].title, "actor": led.events[i].actor} for i in inc]}
    qa = _quarter_share(anoms, seg)
    if qa:
        calcs += qa.calcs
        facts["q_series"] = qa.data["series"]
    span = fmt.days(led.events[inc[0]].date, led.events[inc[-1]].date) / 30.4
    facts["months_between_incidents"] = round(span)
    prov = [o for o in led.observations if o.metric == "provision" and o.dims.get("item") == prod and o.entity == led.company]
    if prov:
        p = prov[0]
        facts["provision"] = {"value": p.value, "unit": p.unit, "period": p.period}
        np_ = led.get("net_profit", p.period)
        if np_:
            c = led.calc(f"{prod} provision as % of net profit, {p.period}", "provision / net profit x 100",
                         M.pct(p.value, np_.value), "%", [p, np_])
            calcs.append(c.id)
            facts["provision_pct_profit"] = c.value
    for g in led.guidance:
        if g.subject == prod and g.before and g.after is not None:
            # Guidance figures are a cited range; encode as observations so the calc is traceable.
            ob = Observation(led.company, f"guidance_{g.metric}", g.period, g.before, g.unit, g.evidence[0], {"state": "before"})
            oa = Observation(led.company, f"guidance_{g.metric}", g.period, g.after, g.unit, g.evidence[0], {"state": "after"})
            c = led.calc(f"Cut to {prod} {g.period} plan", "after / before - 1", M.growth(g.after, g.before), "%", [oa, ob])
            calcs.append(c.id)
            facts["guidance_cut"] = {"before": g.before, "after": g.after, "unit": g.unit, "period": g.period, "pct": c.value,
                                    "label": g.label or f"{g.after:g}"}
    partners = [ln for ln in led.links if ln.product == prod]
    facts["partners"] = [{"name": ln.b, "type": ln.type, "note": ln.note} for ln in partners]
    acq = _events(led, lambda ev: ev.kind == "acquisition" and any(g.subject in ev.tags for g in gainers))
    facts["acquisitions"] = [led.events[i].title for i in acq]
    if "ex_growth" not in facts:
        ex = geo_mix_shift(led, anoms)
        if ex:
            for k in ("ex_growth", "ex_growth_calc", "seg_growth", "gainers", "latest_period", "first_period"):
                if k in ex[0].facts:
                    facts[k] = ex[0].facts[k]
            calcs += [c for c in ex[0].calcs if c not in calcs]
    s = dec.data["series"]
    ps = list(s)
    peak = dec.data["peak_period"]
    inc_txt = "; ".join(f"{fmt.nice_date(x['date'], x['precision'])}: {x['title'].split(';')[0]}" for x in facts["incidents"][:3])
    stmt = (f"As {', '.join(sorted(old)) or 'its main product'} fades, {seg} fell from {s[peak]:.1f}% of revenue ({peak}) to "
            f"{s[ps[-1]]:.1f}% ({ps[-1]})" + (f" and {list(qa.data['series'].values())[-1]:.1f}% in {list(qa.data['series'])[-1]}" if qa else "")
            + f"; the lead replacement, {prod}, has hit {len(inc)} setbacks in {facts['months_between_incidents']} months ({inc_txt}).")
    support = set(_ev_of_calcs(led, calcs))
    for i in inc:
        support |= set(led.events[i].evidence)
    for ln in partners:
        support |= set(ln.evidence)
    contra = [c.evidence for c in led.claims if c.topic in (prod, "base_business")]
    h = Hypothesis(_hid(), "replacement_concentration",
                   f"{prod[:1].upper() + prod[1:]}, not {', '.join(sorted(old)) or 'the old driver'}, is now the swing factor, and it is fragile", stmt, facts,
                   support=sorted(support), contra=sorted(set(contra)), calcs=calcs, events=inc + acq)
    mat = s[peak] / 100.0 + (facts.get("provision_pct_profit", 0) / 100.0)
    h.scores["materiality"] = min(1.0, mat)
    h.facts["core_terms"] = [prod.lower(), "setback", "replacement", "concentrat"]
    return [h]


# ------------------------------------------------- third-party plant risk

def counterparty_regulatory(led: EvidenceLedger, anoms: list[Anomaly]) -> list[Hypothesis]:
    out: list[Hypothesis] = []
    for ln in led.links:
        if ln.type not in ("manufacturing_partner", "supplier", "licensor") or not ln.product:
            continue
        reg = _events(led, lambda ev: ev.kind == "regulatory" and ev.actor not in ("company",) and ln.b in ev.tags
                      and "incident" in ev.tags)
        if not reg:
            continue
        disc = _events(led, lambda ev: ev.kind == "disclosure" and ev.actor == "company" and ln.product in ev.tags)
        media = _events(led, lambda ev: ev.kind == "media" and ln.product in ev.tags)
        seg = ln.segment or next((t for i in disc for t in led.events[i].tags if t in led.dim_values("revenue_segment", "segment")), None)
        step = next((a for a in anoms if a.type == "segment_step_down" and a.subject == seg), None)
        first_reg = led.events[reg[0]]
        facts: dict = {"partner": ln.b, "product": ln.product, "segment": seg, "link_note": ln.note,
                       "regulator_events": [{"date": led.events[i].date, "title": led.events[i].title, "actor": led.events[i].actor}
                                            for i in reg]}
        calcs: list[str] = []
        if disc:
            d = led.events[disc[0]]
            facts["disclosure"] = {"date": d.date, "title": d.title}
            facts["lag_regulator_to_disclosure_days"] = fmt.days(first_reg.date, d.date)
        if media:
            facts["media_first"] = {"date": led.events[media[0]].date, "title": led.events[media[0]].title}
        if step:
            sp = step.data["step_period"]
            facts["step"] = {"from": step.data["before"], "to": step.data["after"], "period": sp, "unit": step.data["unit"],
                             "pct": step.magnitude, "latest": step.data["latest"], "latest_period": step.data["latest_period"]}
            calcs += step.calcs
            q = led.series("revenue_segment", segment=seg, freq="Q")
            after = [o for o in q if periods.sort_key(o.period) >= periods.sort_key(sp)]
            facts["post_step_range"] = [min(o.value for o in after), max(o.value for o in after)]
            facts["post_step_evidence"] = sorted({o.evidence for o in after})
            if disc:
                qend = periods.end_date(sp).isoformat()
                facts["quarter_end_before_disclosure_days"] = fmt.days(qend, facts["disclosure"]["date"])
                facts["reg_inside_step_quarter"] = periods.parse_date(first_reg.date) <= periods.end_date(sp)
            pr = led.series("product_revenue", product=ln.product)
            if pr:
                p = pr[-1]
                qrun = p.value / 4.0
                before_o = next(o for o in q if o.period != sp and periods.sort_key(o.period) < periods.sort_key(sp)
                                and o.value == step.data["before"])
                after_o = next(o for o in q if o.period == sp)
                c_run = led.calc(f"{ln.product} quarterly run-rate ({p.period} estimate / 4)", "annual / 4", qrun, p.unit, [p])
                c_gap = led.calc(f"{seg} step-down {before_o.period}->{sp}", "before - after",
                                 before_o.value - after_o.value, p.unit, [before_o, after_o])
                c_ex = led.calc(f"Share of the step-down a full loss of {ln.product} would explain", "run-rate / step-down x 100",
                                M.pct(qrun, before_o.value - after_o.value), "%", [c_run, c_gap])
                calcs += [c_run.id, c_gap.id, c_ex.id]
                facts["product_run_rate"] = qrun
                facts["explained_pct"] = c_ex.value
                share = led.series("product_share_of_segment", product=ln.product)
                if share:
                    facts["product_share"] = {"value": share[-1].value, "period": share[-1].period, "note": share[-1].note}
            tgt = led.series("revenue_target_annualised", segment=seg)
            if tgt:
                latest_o = q[-1]
                ann = Observation(led.company, "revenue_segment_annualised", latest_o.period, latest_o.value * 4, latest_o.unit,
                                  latest_o.evidence, {"segment": seg}, derived=True)
                c_up = led.calc(f"Uplift needed from {latest_o.period} run-rate to {seg} target", "target / (latest quarter x 4) - 1",
                                M.growth(tgt[-1].value, latest_o.value * 4), "%", [tgt[-1], ann])
                calcs.append(c_up.id)
                facts["target"] = {"value": tgt[-1].value, "unit": tgt[-1].unit, "uplift_pct": c_up.value}
        guid = [c for c in led.claims if c.topic == ln.product]
        facts["guidance_claims"] = [c.text for c in guid]
        support = set(ln.evidence) | set(_ev_of_calcs(led, calcs)) | set(facts.get("post_step_evidence", []))
        res = _events(led, lambda ev: ev.kind == "results" and ln.product in ev.tags)
        for i in reg + disc + res:
            support |= set(led.events[i].evidence)
        step_txt = ""
        if "step" in facts:
            st = facts["step"]
            lo, hi = facts["post_step_range"]
            step_txt = (f"; {seg} revenue fell from {fmt.money(st['from'], st['unit'])} to {fmt.money(st['to'], st['unit'])} in {st['period']}"
                        + (f", a quarter that closed {facts['quarter_end_before_disclosure_days']} days before the company disclosed the problem"
                           if facts.get("quarter_end_before_disclosure_days", -1) > 0 else "")
                        + f", and has stayed at {fmt.money(lo, st['unit'])}–{fmt.money(hi, st['unit'])} since")
        share_txt = f" (~{facts['product_share']['value']:.0f}% of {seg} sales in {facts['product_share']['period']})" if "product_share" in facts else ""
        stmt = (f"A plant the company does not own drove its sharpest {seg or 'revenue'} decline: {ln.b}, maker of {ln.product}{share_txt}, "
                f"closed an FDA inspection with observations on {fmt.nice_date(first_reg.date)}"
                + (f", {facts['lag_regulator_to_disclosure_days']} days before the company's disclosure" if disc else "") + step_txt + ".")
        h = Hypothesis(_hid(), "counterparty_regulatory", f"Third-party plant ({ln.b}) behind the {seg} decline", stmt, facts,
                       support=sorted(support), contra=[c.evidence for c in led.claims if c.topic in ("north_america",)],
                       calcs=calcs, events=reg + disc + media + res)
        h.scores["materiality"] = min(1.0, abs(facts.get("step", {}).get("pct", 10)) / 35.0)
        h.facts["core_terms"] = [ln.b.lower(), ln.product.lower(), "fda"]
        out.append(h)
    return out


# ----------------------------------------------------------- payor policy

def payor_policy(led: EvidenceLedger, anoms: list[Anomaly]) -> list[Hypothesis]:
    gaps = [a for a in anoms if a.type == "growth_gap" and a.data["basis"].startswith("ex_")]
    if not gaps:
        return []
    gap = max(gaps, key=lambda a: abs(a.magnitude))
    spec = gap.data["basis"][3:]
    shift = next((a for a in anoms if a.type == "specialty_share_shift" and a.subject == spec), None)
    stall = next((a for a in anoms if a.type == "unit_price_stall"), None)
    pol = _events(led, lambda ev: ev.kind == "policy")
    act = _events(led, lambda ev: ev.kind == "operations" and spec in ev.tags and "incident" in ev.tags)
    if not pol or not act:
        return []
    calcs = list(gap.calcs) + (shift.calcs if shift else [])
    facts: dict = {"specialty": spec, "gap_period": gap.periods[0], "reported": gap.data["reported"], "adjusted": gap.data["adjusted"],
                   "policies": [{"date": led.events[i].date, "precision": led.events[i].date_precision, "title": led.events[i].title,
                                 "short": led.events[i].short}
                                for i in pol],
                   "action": {"date": led.events[act[0]].date, "precision": led.events[act[0]].date_precision,
                              "title": led.events[act[0]].title}}
    if shift:
        s0, s1 = shift.data["from"], shift.data["to"]
        facts["share"] = {"from": s0, "to": s1, "periods": shift.periods}
        g = gap.data["reported"] / 100.0
        o_from = led.series("specialty_share_ip", specialty=spec)
        rep = led.get("revenue_growth_yoy", gap.periods[0], basis="reported")
        c_spec = led.calc(f"Implied {spec} revenue growth, {gap.periods[0]}", "(share1 / share0) x (1 + g) - 1",
                          100 * ((s1 / s0) * (1 + g) - 1), "%", o_from + [rep])
        c_rest = led.calc(f"Implied non-{spec} revenue growth, {gap.periods[0]}", "((1 - share1) / (1 - share0)) x (1 + g) - 1",
                          100 * (((1 - s1 / 100) / (1 - s0 / 100)) * (1 + g) - 1), "%", o_from + [rep])
        calcs += [c_spec.id, c_rest.id]
        facts.update(implied_spec_growth=c_spec.value, implied_rest_growth=c_rest.value)
    if stall:
        facts["arpob_series"] = stall.data["series"]
    ben = [o for o in led.observations if o.metric == "policy_benefit_annual" and o.entity == led.company]
    rev = led.get("revenue_total", gap.periods[0])
    if ben and rev:
        c_b = led.calc("Policy tariff benefit as % of quarterly revenue", "(annual benefit / 4) / quarterly revenue x 100",
                       M.pct(ben[0].value / 4, rev.value), "%", [ben[0], rev])
        calcs.append(c_b.id)
        facts["benefit"] = {"policy": ben[0].dims.get("policy"), "annual": ben[0].value, "unit": ben[0].unit, "pct_of_q_revenue": c_b.value}
        ag = led.get("arpob_growth_yoy", gap.periods[0])
        if ag:
            bo = Observation(led.company, "policy_benefit_pct", gap.periods[0], c_b.value, "%", ben[0].evidence, derived=True)
            c_core = led.calc(f"ARPOB growth excluding the tariff benefit, {gap.periods[0]}", "ARPOB growth - benefit % (approx.)",
                              ag.value - c_b.value, "pp", [ag, bo])
            calcs.append(c_core.id)
            facts["arpob_core_growth"] = c_core.value
            facts["arpob_reported_growth"] = ag.value
    peer = [o for o in led.observations if o.entity.startswith("peer:") and o.metric == "arpob_growth_yoy" and o.period == gap.periods[0]]
    if peer:
        facts["peer_unit_price"] = [{"peer": o.entity[5:], "value": o.value} for o in peer]
    support = set(_ev_of_calcs(led, calcs))
    for i in pol + act:
        support |= set(led.events[i].evidence)
    for ln in led.links:
        if ln.type == "payor":
            support |= set(ln.evidence)
    stall_txt = ""
    if stall:
        ser = stall.data["series"]
        stall_txt = f"ARPOB growth slid to {', '.join(f'{v:+.1f}% ({p})' for p, v in ser.items())}; "
    act_title = facts["action"]["title"]
    stmt = (f"{stall_txt}the cause is a payor-policy change, not demand: after the {facts['policies'][0]['title'].split(':')[0]} "
            f"({fmt.nice_date(facts['policies'][0]['date'], facts['policies'][0]['precision'])}), the company "
            f"{act_title[0].lower() + act_title[1:]} from {fmt.nice_date(facts['action']['date'], facts['action']['precision'])}; "
            + (f"{spec} fell from {facts['share']['from']:.0f}% to {facts['share']['to']:.0f}% of inpatient revenue, " if shift else "")
            + f"so reported growth ({facts['reported']:.0f}%) understates the rest of the business ({facts['adjusted']:.0f}%).")
    h = Hypothesis(_hid(), "payor_policy", f"A government payor rule, not demand, is behind the {spec} drag", stmt, facts,
                   support=sorted(support), contra=[c.evidence for c in led.claims if c.topic in (spec,)],
                   calcs=calcs, events=pol + act)
    h.scores["materiality"] = min(1.0, abs(gap.magnitude) / 8.0 + (abs(shift.magnitude) / 20.0 if shift else 0))
    h.facts["core_terms"] = [spec.lower(), "cghs", "arpob", "policy"]
    return [h]


# --------------------------------------------------------- capacity gaps

def capacity_gap(led: EvidenceLedger, anoms: list[Anomaly]) -> list[Hypothesis]:
    caps = [a for a in anoms if a.type == "capacity_gap"]
    if not caps:
        return []
    lo = min(caps, key=lambda a: a.data["target"])
    hi = max(caps, key=lambda a: a.data["target"])
    calcs = sorted(set(lo.calcs + hi.calcs))
    guid = _events(led, lambda ev: ev.kind == "guidance" and "capacity" in ev.tags)
    delays = _events(led, lambda ev: "delay" in ev.tags)
    facts = {"by": lo.data["by"], "target_low": lo.data["target"], "target_high": hi.data["target"],
             "current": lo.data["current"], "current_period": lo.data["current_period"], "trailing": lo.data["trailing"],
             "need_low": lo.data["need"], "need_high": hi.data["need"], "accel_low": lo.magnitude, "accel_high": hi.magnitude,
             "target_history": [{"date": led.events[i].date, "title": led.events[i].title} for i in guid],
             "delays": [{"date": led.events[i].date, "title": led.events[i].title} for i in delays],
             "months_left": lo.data["months_left"]}
    support = set(_ev_of_calcs(led, calcs))
    for i in guid + delays:
        support |= set(led.events[i].evidence)
    stmt = (f"Operational beds rose by {facts['trailing']:,.0f} in the 12 months to {facts['current_period']} (to {facts['current']:,.0f}); "
            f"reaching the {facts['target_low']:,.0f}–{facts['target_high']:,.0f} target by {facts['by']} needs "
            f"{facts['need_low']:,.0f}–{facts['need_high']:,.0f} beds a year, {facts['accel_low']:.1f}–{facts['accel_high']:.1f}x the current pace"
            + (f", while the headline target has been restated {len(guid)} times" if len(guid) > 1 else "") + ".")
    h = Hypothesis(_hid(), "capacity_gap", "Announced capacity is running well ahead of commissioned capacity", stmt, facts,
                   support=sorted(support), contra=[c.evidence for c in led.claims if c.topic == "capacity"],
                   calcs=calcs, events=guid + delays)
    h.scores["materiality"] = min(1.0, (lo.magnitude - 1) / 4.0)
    h.facts["core_terms"] = ["beds", "target", "capacity", "commission"]
    return [h]


TEMPLATES = {
    "geo_mix_shift": geo_mix_shift,
    "replacement_concentration": replacement_concentration,
    "counterparty_regulatory": counterparty_regulatory,
    "payor_policy": payor_policy,
    "capacity_gap": capacity_gap,
}


def generate(led: EvidenceLedger, anoms: list[Anomaly], angles: list[str]) -> list[Hypothesis]:
    hyps: list[Hypothesis] = []
    for a in angles:
        fn = TEMPLATES.get(a)
        if not fn:
            led.log("hypothesis", f"skip:{a}", "no deterministic template; LLM mode only")
            continue
        made = fn(led, anoms)
        if not made:
            led.log("hypothesis", f"none:{a}", "evidence did not support this angle")
        for h in made:
            led.log("hypothesis", f"proposed:{h.id}", f"[{a}] {h.statement}", h.support[:6])
        hyps += made
    return hyps
