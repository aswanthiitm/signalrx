"""The skeptic: tries to break every hypothesis before it can be reported.

Each alternative explanation in the knowledge pack is implemented as a concrete
test against the ledger. Where possible the test re-runs the numbers under the
alternative (add the one-off back, strip the acquisition out, compare with
peers) rather than just asserting a counterpoint. In live mode the same
questions are also turned into adversarial searches (adversarial_queries).
"""
from __future__ import annotations

import re

from .. import fmt, periods
from ..analytics import metrics as M
from ..knowledge import load_pack
from ..ledger import EvidenceLedger
from ..models import Check, Hypothesis

ONE_OFF = re.compile(r"one[- ]time|one[- ]off|shelf[- ]stock|exceptional|non[- ]recurring", re.I)


def _peer_growth(led: EvidenceLedger, segment: str, period: str) -> list[tuple[str, float, str]]:
    return [(o.entity[5:], o.value, o.evidence) for o in led.observations
            if o.entity.startswith("peer:") and o.metric == "revenue_segment_growth_yoy"
            and o.dims.get("segment") == segment and o.period == period]


def _own_growth(led: EvidenceLedger, segment: str, period: str) -> float | None:
    now = led.get("revenue_segment", period, segment=segment)
    prev = led.get("revenue_segment", periods.prior_year(period), segment=segment)
    if now and prev and now.unit == prev.unit:
        return M.growth(now.value, prev.value)
    g = led.get("revenue_segment_growth_yoy", period, segment=segment)
    return g.value if g else None


def check_sector_wide(led: EvidenceLedger, h: Hypothesis) -> Check | None:
    q = load_pack()["alternatives"]["sector_wide"]["question"]
    seg = h.facts.get("segment")
    if h.angle == "payor_policy":
        peers = h.facts.get("peer_unit_price", [])
        own = h.facts.get("arpob_reported_growth")
        if not peers or own is None:
            return Check("sector_wide", q, "inconclusive", "No peer unit-price data in the ledger.")
        ev = [o.evidence for o in led.observations if o.entity.startswith("peer:") and o.metric == "arpob_growth_yoy"]
        better = [p for p in peers if p["value"] > own]
        if better:
            return Check("sector_wide", q, "survives",
                         "Company-specific: peers grew revenue per patient faster in the same quarter ("
                         + ", ".join(f"{p['peer']} {p['value']:+.0f}%" for p in better) + f" vs {own:+.0f}%).", ev)
        return Check("sector_wide", q, "weakens", "Peers show the same unit-price weakness.", ev)
    if not seg:
        return None
    qs = led.series("revenue_segment", segment=seg, freq="Q")
    period = qs[-1].period if qs else None
    peers = _peer_growth(led, seg, period) if period else []
    if not peers:
        return Check("sector_wide", q, "inconclusive", f"No peer {seg} data for {period}.")
    own = _own_growth(led, seg, period)
    shared = [p for p in peers if own is not None and p[1] < 0 and abs(p[1]) >= 0.5 * abs(own)]
    detail = (f"{seg} {period}: company {own:+.1f}% vs " + ", ".join(f"{p[0]} {p[1]:+.1f}%" for p in peers) + ". ")
    ev = [p[2] for p in peers]
    if shared and h.angle in ("replacement_concentration", "counterparty_regulatory"):
        return Check("sector_wide", q, "survives", detail +
                     "The decline itself is partly a sector-wide cliff, but the hypothesis rests on incidents tied to the company's "
                     "own product / partner, which peers do not share.", ev)
    if shared:
        return Check("sector_wide", q, "weakens", detail + "Peers show a comparable decline, so part of the move is sector-wide.", ev)
    return Check("sector_wide", q, "survives", detail + "Peers did not fall as much: the move is company-specific.", ev)


def check_inorganic(led: EvidenceLedger, h: Hypothesis) -> Check | None:
    q = load_pack()["alternatives"]["inorganic"]["question"]
    acq = [(i, ev) for i, ev in led.events_where(kind="acquisition")]
    if not acq:
        return Check("inorganic", q, "survives", "No acquisition or divestment in the ledger for the relevant segments.")
    gain_segs = {g["segment"] for g in h.facts.get("gainers", [])}
    rel = [(i, ev) for i, ev in acq if gain_segs & set(ev.tags)]
    if not rel:
        return Check("inorganic", q, "inconclusive", "Acquisitions exist but are not tied to the segments in this hypothesis.")
    ev_ids = [e for _, ev in rel for e in ev.evidence]
    titles = "; ".join(ev.title for _, ev in rel)
    if h.angle == "geo_mix_shift":
        return Check("inorganic", q, "weakens", f"Part of the gain is bought, not built: {titles}.", ev_ids)
    return Check("inorganic", q, "survives", f"Context: part of the replacement growth was acquired ({titles}), which leaves "
                 "fewer organic levers and makes the remaining new launches matter more.", ev_ids)


def check_one_off(led: EvidenceLedger, h: Hypothesis) -> Check | None:
    q = load_pack()["alternatives"]["one_off"]["question"]
    seg = h.facts.get("segment")
    adj = [o for o in led.observations if o.metric == "one_off_adjustment" and o.dims.get("segment") == seg and o.entity == led.company]
    if adj and h.facts.get("series"):
        a = adj[0]
        rows = [o for o in led.observations if o.entity == led.company and o.metric == "revenue_segment" and o.period == a.period
                and o.unit == a.unit]
        if rows:
            total = sum(o.value for o in rows) + a.value
            segv = next(o for o in rows if o.dims.get("segment") == seg).value + a.value
            share = M.pct(segv, total)
            c = led.calc(f"{seg} share {a.period} with one-off added back", "(segment + one-off) / (total + one-off) x 100", share, "%", rows + [a])
            h.calcs.append(c.id)
            peak = h.facts["series"][h.facts["peak_period"]]
            still = peak - share
            verdict = "survives" if still >= load_pack()["materiality"]["mix_shift_pp"] else "weakens"
            return Check("one_off", q, verdict,
                         f"Adding back the {fmt.money(a.value, a.unit)} one-time item lifts {seg}'s {a.period} share only to {share:.1f}% "
                         f"(vs {peak:.1f}% at the peak): the shift is {still:.1f}pp even after the one-off.", [a.evidence])
    texts = [led.evidence[e].text for e in h.support if e in led.evidence]
    hits = [t for t in texts if ONE_OFF.search(t)]
    if h.facts.get("provision"):
        return Check("one_off", q, "survives", "The provision is a one-time charge, but the hypothesis does not rest on it: "
                     "the plan cut and repeated setbacks are not one-offs.", [])
    if hits:
        return Check("one_off", q, "weakens", "Supporting passages mention one-time items: " + hits[0][:160], [])
    return Check("one_off", q, "survives", "No one-time item is attached to the figures behind this hypothesis.")


def check_temporary(led: EvidenceLedger, h: Hypothesis) -> Check | None:
    q = load_pack()["alternatives"]["temporary"]["question"]
    subject = h.facts.get("product") or h.facts.get("specialty")
    fut = [(i, ev) for i, ev in led.events_where(kind="guidance") if "future" in ev.tags and (subject in ev.tags or ev.subject == subject)]
    claims = [c for c in led.claims if c.topic == subject]
    if fut:
        ev = fut[0][1]
        return Check("temporary", q, "weakens",
                     f"Management has a dated fix: '{ev.title}' ({fmt.nice_date(ev.date, ev.date_precision)}). If it lands on time, "
                     "the financial drag fades; this becomes a falsification test.",
                     ev.evidence + [c.evidence for c in claims])
    perm = [c for c in claims if re.search(r"no .*contribution|structural|zero", c.text, re.I)]
    if perm:
        return Check("temporary", q, "survives", f"Management itself now treats it as lasting: '{perm[0].text}'", [perm[0].evidence])
    return Check("temporary", q, "inconclusive", "No dated remediation plan found.")


def check_already_mainstream(led: EvidenceLedger, h: Hypothesis) -> Check:
    q = load_pack()["alternatives"]["already_mainstream"]["question"]
    terms = [t for t in h.facts.get("core_terms", []) if t]
    heads = [(e.id, e.text) for e in led.evidence.values() if e.kind == "headline"]
    heads += [(ev.evidence[0], ev.title) for ev in led.events if ev.kind == "media"]
    hits = []
    for eid, text in heads:
        t = text.lower()
        n = sum(1 for term in terms if term in t)
        if n >= 2:
            hits.append((eid, text, n))
    if hits:
        eid, text, n = max(hits, key=lambda x: x[2])
        return Check("already_mainstream", q, "weakens", f"A headline already carries the core terms: \"{text[:120]}\". "
                     "The finding must add something the headline lacks (timing, sizing or a cross-source link).", [eid])
    return Check("already_mainstream", q, "survives",
                 f"None of {len(heads)} headlines in the ledger connects these facts ({', '.join(terms[:3])}).", [e for e, _ in heads][:4])


def check_other_driver(led: EvidenceLedger, h: Hypothesis) -> Check | None:
    q = load_pack()["alternatives"]["other_driver"]["question"]
    seg = h.facts.get("segment")
    step = h.facts.get("step")
    if not seg or not step:
        return None
    others = [ev for _, ev in led.events_where(kind="loe") if seg in ev.tags and ev.subject != h.facts.get("product")]
    expl = h.facts.get("explained_pct")
    if others:
        ev = others[0]
        after_step = periods.parse_date(ev.date) > periods.end_date(step["period"])
        if after_step:
            txt = (f"A second driver hit {seg}: {ev.title} ({fmt.nice_date(ev.date)}). It lands after the {step['period']} quarter closed, "
                   "so it explains the failure to recover in later quarters rather than the step itself.")
        else:
            txt = f"A second driver hit {seg} in the same window: {ev.title} ({fmt.nice_date(ev.date)})."
        if expl is not None:
            ce = next(c for c in led.calcs.values() if c.value == expl)
            cu = led.calc("Share of the step-down not explained by the product", "100 - explained %", 100 - expl, "%", [ce])
            h.calcs.append(cu.id)
            txt += (f" A full loss of {h.facts['product']} (~{fmt.money(h.facts['product_run_rate'], step['unit'])}/quarter) explains "
                    f"about {expl:.0f}% of the {step['period']} step-down; the remaining {100 - expl:.0f}% needs another explanation "
                    "(earlier erosion of the second driver, base-business price erosion).")
        return Check("other_driver", q, "weakens", txt, ev.evidence)
    return Check("other_driver", q, "survives", "No other dated driver found for the same segment and window.")


def check_breadth(led: EvidenceLedger, h: Hypothesis) -> Check | None:
    """Replacement-concentration specific: is growth outside the decliner broad-based?"""
    if h.angle != "replacement_concentration":
        return None
    q = "Is the replacement actually broad-based rather than concentrated in one product?"
    latest = h.facts.get("latest_period")
    if not latest:
        return None
    out = []
    for seg in led.dim_values("revenue_segment", "segment"):
        if seg == h.facts["segment"]:
            continue
        g = _own_growth(led, seg, latest)
        if g is not None:
            out.append((seg, g))
    strong = [s for s in out if s[1] >= 10]
    if len(strong) >= 2:
        return Check("breadth", q, "weakens",
                     f"Growth outside {h.facts['segment']} is broad (" + ", ".join(f"{s} {g:+.0f}%" for s, g in out) + f", {latest}). "
                     f"So {h.facts['product']} is the swing factor for the next year, not the whole replacement: the finding is "
                     "reframed accordingly.", [])
    return Check("breadth", q, "survives", f"Growth outside {h.facts['segment']} is narrow: " + ", ".join(f"{s} {g:+.0f}%" for s, g in out))


def check_occupancy_mix(led: EvidenceLedger, h: Hypothesis) -> list[Check]:
    if h.angle != "payor_policy":
        return []
    pack = load_pack()["alternatives"]
    out = []
    occ = led.series("occupancy", freq="Q")
    if len(occ) >= 2:
        rng = (min(o.value for o in occ), max(o.value for o in occ))
        verdict = "survives" if rng[1] - rng[0] <= 3 else "weakens"
        out.append(Check("occupancy", pack["occupancy"]["question"], verdict,
                         f"Occupancy held in a {rng[0]:.0f}-{rng[1]:.0f}% band across {occ[0].period}-{occ[-1].period}; "
                         "a volume collapse does not explain the unit-price stall.", [o.evidence for o in occ]))
    texts = [(e, led.evidence[e].text) for e in h.support if e in led.evidence]
    pre = [(e, t) for e, t in texts if re.search(r"pre-commissioning|new (capacity|beds)", t, re.I)]
    if pre:
        out.append(Check("new_capacity", pack["new_capacity"]["question"], "weakens",
                         "New-capacity costs are a second, smaller drag on margins cited in the same note. Source: "
                         + pre[0][1][:150] + "...", [pre[0][0]]))
    return out


def check_phasing(led: EvidenceLedger, h: Hypothesis) -> Check | None:
    if h.angle != "capacity_gap":
        return None
    q = load_pack()["alternatives"]["phasing"]["question"]
    delays = h.facts.get("delays", [])
    sched = [e for e in led.evidence.values() if re.search(r"by FY\d\d|phased|next two quarters", e.text)]
    if delays:
        return Check("phasing", q, "survives", f"Some of the gap is published phasing ({len(sched)} project schedules), but "
                     f"{len(delays)} dated delay(s) are on record: " + "; ".join(d["title"] for d in delays), [e.id for e in sched][:3])
    if sched:
        return Check("phasing", q, "weakens", "The gap is consistent with a published, phased commissioning schedule.", [e.id for e in sched][:3])
    return Check("phasing", q, "inconclusive", "No project-level schedule in the ledger.")


def check_low_margin(led: EvidenceLedger, h: Hypothesis) -> Check | None:
    texts = [(e, led.evidence[e].text) for e in set(h.support) | set(h.contra) if e in led.evidence]
    hit = next(((e, t) for e, t in texts if re.search(r"low[- ]margin", t, re.I)), None)
    if not hit:
        return None
    return Check("low_margin", "Does the revenue effect overstate the profit effect?", "weakens",
                 "The lost revenue is described as low-margin, so the earnings damage is smaller than the revenue damage. Source: \""
                 + hit[1][:140] + "...\"", [hit[0]])


def check_conflicts(led: EvidenceLedger, h: Hypothesis) -> Check | None:
    ev = set(h.support)
    rel = [c for c in led.conflicts if {c.kept.evidence, *[d.evidence for d in c.dropped]} & ev]
    if not rel:
        return None
    return Check("data_conflicts", "Do sources disagree on any figure this rests on?", "survives",
                 "Resolved: " + " | ".join(c.reason for c in rel), [c.kept.evidence for c in rel])


CHECKS = [check_sector_wide, check_inorganic, check_one_off, check_temporary, check_already_mainstream,
          check_other_driver, check_breadth, check_phasing, check_low_margin, check_conflicts]


def challenge(led: EvidenceLedger, h: Hypothesis) -> Hypothesis:
    alts = next((a["alternatives"] for a in load_pack()["angles"] if a["id"] == h.angle), [])
    wanted = set(alts) | {"already_mainstream", "breadth", "phasing", "data_conflicts", "other_driver", "low_margin", "conflicts"}
    for fn in CHECKS:
        name = fn.__name__.replace("check_", "")
        if name not in wanted:
            continue
        c = fn(led, h)
        if c:
            h.checks.append(c)
    h.checks += check_occupancy_mix(led, h)
    for c in h.checks:
        if c.verdict in ("weakens", "refutes"):
            h.contra = sorted(set(h.contra) | set(c.evidence))
        led.log("skeptic", f"{h.id}:{c.name}", f"{c.verdict.upper()} - {c.detail}", c.evidence)
    if any(c.verdict == "refutes" for c in h.checks):
        h.status, h.rejection_reason = "rejected", next(c.detail for c in h.checks if c.verdict == "refutes")
    else:
        h.status = "tested"
    return h


def adversarial_queries(company: str, h: Hypothesis) -> list[str]:
    """Searches the live skeptic runs to look for evidence against a hypothesis."""
    seg = h.facts.get("segment") or h.facts.get("specialty") or ""
    prod = h.facts.get("product") or h.facts.get("partner") or ""
    return [q.strip() for q in [
        f"{seg} decline Indian pharma peers same quarter",
        f"{company} {seg} one-time adjustment",
        f"{company} {prod} resolved resumed supply",
        f"{company} management explanation {seg}",
        f"{company} acquisition contribution {seg}",
    ] if q.strip()]
