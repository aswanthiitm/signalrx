"""The verifier: nothing reaches the report unless it traces to the ledger.

1. Recompute every reported growth rate from the underlying levels and drop
   reports that disagree (outlets often get the sign or the base wrong).
2. Resolve remaining conflicts by source hierarchy (ledger.resolve_conflicts).
3. After writing: every number in the prose must match a ledger value, a
   calculation, or a fact (within rounding), and every cited evidence id must
   resolve to a source with a URL. Major claims need two independent families.
4. In live mode, every quoted passage must be found in the fetched document
   (tools/documents.verify_quote); in replay mode passages are marked as
   "as retrieved".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import periods
from ..analytics import metrics as M
from ..ledger import EvidenceLedger

GROWTH_TOL_PP = 1.5


def recompute_growth(led: EvidenceLedger) -> list[str]:
    notes: list[str] = []
    keep = []
    for o in led.observations:
        level_metric = {"revenue_growth_yoy": "revenue_total", "revenue_segment_growth_yoy": "revenue_segment"}.get(o.metric)
        if not level_metric or o.entity != led.company or (o.metric == "revenue_growth_yoy" and o.dims.get("basis") != "reported"):
            keep.append(o)
            continue
        dims = {k: v for k, v in o.dims.items() if k == "segment"}
        now = led.get(level_metric, o.period, **dims)
        prev = led.get(level_metric, periods.prior_year(o.period), **dims)
        if not (now and prev) or now.unit != prev.unit:
            keep.append(o)
            continue
        g = M.growth(now.value, prev.value)
        src = led.source_of(o.evidence)
        if abs(g - o.value) > GROWTH_TOL_PP:
            ev = led.evidence[o.evidence]
            ev.verified = False
            ev.note = f"reported {o.value:+.1f}% but levels give {g:+.1f}%"
            msg = (f"Dropped {src.publisher}'s {o.metric.replace('_', ' ')} {o.period}{list(dims.values()) or ''} of {o.value:+.1f}%: "
                   f"recomputed from filed levels it is {g:+.1f}% ({now.value:,.0f} vs {prev.value:,.0f})")
            notes.append(msg)
            led.log("verify", "growth-recompute:drop", msg, [o.evidence, now.evidence, prev.evidence])
        else:
            led.evidence[o.evidence].verified = True
            keep.append(o)
            led.log("verify", "growth-recompute:ok", f"{src.publisher} {o.metric} {o.period} {o.value:+.1f}% matches levels ({g:+.1f}%)",
                    [o.evidence])
    led.observations = keep
    return notes


@dataclass
class VerificationReport:
    numbers_checked: int = 0
    numbers_unmatched: list[str] = field(default_factory=list)
    citations_checked: int = 0
    citations_broken: list[str] = field(default_factory=list)
    families_for_discovery: list[str] = field(default_factory=list)
    passed: bool = True
    notes: list[str] = field(default_factory=list)


_NUM = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*\.?\d*")


def _allowed(led: EvidenceLedger, facts: dict) -> list[float]:
    vals: list[float] = []

    def add(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            vals.extend([float(v), abs(float(v))])
        elif isinstance(v, dict):
            for x in v.values():
                add(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                add(x)
    for o in led.observations:
        add(o.value)
        if o.unit == "INR_mn":
            add(o.value / 10)       # crore
            add(o.value / 1000)     # billion
    for c in led.calcs.values():
        add(c.value)
    # Figures quoted verbatim from a source passage or a dated event are traceable too.
    for text in [e.text for e in led.evidence.values()] + [ev.title for ev in led.events]:
        for m in _NUM.finditer(text):
            try:
                add(float(m.group(0).rstrip(".,").replace(",", "").lstrip("+")))
            except ValueError:
                pass
    add(facts)
    for k in ("provision", "benefit"):
        if isinstance(facts.get(k), dict):
            for key in ("value", "annual"):
                if key in facts[k]:
                    add(facts[k][key] / 10)
                    add(facts[k][key] / 1000)
    for k in ("product_run_rate",):
        if k in facts:
            add(facts[k])
    return vals


def _is_calendar(tok: str, text: str, start: int) -> bool:
    n = tok.replace(",", "")
    before = text[max(0, start - 3):start]
    if re.fullmatch(r"(19|20)\d\d", n):
        return True
    if before.endswith(("FY", "Q", "H")) or re.match(r"[Q]\d", text[start - 1:start + 1] or ""):
        return True
    return False


def check_numbers(led: EvidenceLedger, text: str, facts: dict) -> tuple[int, list[str]]:
    allowed = _allowed(led, facts)
    unmatched, n = [], 0
    for m in _NUM.finditer(text):
        tok = m.group(0).rstrip(".,")
        if not tok or tok in "+-":
            continue
        if _is_calendar(tok, text, m.start()):
            continue
        # skip day-of-month in dates like "21 Nov 2025" / "3-Oct"
        after = text[m.end():m.end() + 5]
        if re.match(r"[\s-]?(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", after):
            continue
        try:
            v = float(tok.replace(",", "").lstrip("+"))
        except ValueError:
            continue
        n += 1
        ok = any(abs(v - a) <= max(0.051 * max(1.0, abs(a)) if abs(a) < 20 else 0.006 * abs(a), 0.051) for a in allowed)
        ok = ok or any(abs(v - round(a)) < 0.5 for a in allowed if abs(a) >= 20)
        if not ok:
            unmatched.append(tok)
    return n, unmatched


def verify_discovery(led: EvidenceLedger, disc: dict, facts: dict) -> VerificationReport:
    rep = VerificationReport()
    prose = " ".join([disc["discovery"], *disc["why_it_matters"], disc["counterargument"]["text"],
                      *[f["condition"] for f in disc["falsification"]]])
    rep.numbers_checked, rep.numbers_unmatched = check_numbers(led, prose, facts)
    cited: set[str] = set(disc.get("discovery_evidence", []))
    for item in disc["evidence_trail"]:
        cited |= set(item["evidence"])
    cited |= set(disc["counterargument"].get("evidence", []))
    for e in sorted(cited):
        rep.citations_checked += 1
        if e not in led.evidence or not led.source_of(e).url.startswith("http"):
            rep.citations_broken.append(e)
    rep.families_for_discovery = sorted(led.families(disc.get("discovery_evidence", [])))
    if len(rep.families_for_discovery) < 2:
        rep.notes.append("Discovery sentence rests on fewer than two independent source families.")
    rep.passed = not rep.numbers_unmatched and not rep.citations_broken and len(rep.families_for_discovery) >= 2
    led.log("verify", "discovery", f"numbers {rep.numbers_checked} checked, unmatched {rep.numbers_unmatched or 'none'}; "
            f"citations {rep.citations_checked}, broken {rep.citations_broken or 'none'}; families {rep.families_for_discovery}")
    return rep
