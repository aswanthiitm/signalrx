"""Scoring: evidence strength (the Strong / Moderate / Weak grade, with reasons
shown), novelty (would a summariser miss it?), robustness (what survived the
skeptic) and materiality, combined into a rank used to pick the discovery."""
from __future__ import annotations

from .. import periods
from ..ledger import EvidenceLedger
from ..models import Hypothesis

WEIGHTS = {"novelty": 0.35, "evidence": 0.30, "materiality": 0.20, "robustness": 0.15}


def evidence_strength(led: EvidenceLedger, h: Hypothesis) -> tuple[str, float, list[str]]:
    fams = led.families(h.support)
    srcs = {led.source_of(e).id: led.source_of(e) for e in h.support if e in led.evidence}
    primary = [s for s in srcs.values() if s.rank == 1]
    non_company = sorted(f for f in fams if f != "company")
    third_party_record = [s for s in srcs.values() if s.type in ("regulator", "counterparty")]
    weak_only = all(s.rank >= 4 for s in srcs.values())
    weakens = [c for c in h.checks if c.verdict == "weakens"]
    refutes = [c for c in h.checks if c.verdict == "refutes"]
    survives = [c for c in h.checks if c.verdict == "survives"]
    derived_inputs = sum(1 for o in led.observations if o.evidence in h.support and o.derived)

    score = 0.0
    score += 0.40 * min(1.0, len(fams) / 6)
    score += 0.20 if primary else 0.0
    score += 0.10 if third_party_record else 0.0
    score += 0.20 * (len(survives) / max(1, len(h.checks)))
    score += 0.10 if not led.conflicts or all(c.kept.evidence for c in led.conflicts) else 0.0
    score -= 0.25 * len(refutes)
    score -= 0.10 if weak_only else 0.0
    score = max(0.0, min(1.0, score))
    grade = "Strong" if score >= 0.72 and not refutes else "Moderate" if score >= 0.5 and not refutes else "Weak"

    reasons = [
        f"{len(srcs)} sources across {len(fams)} independent families ({', '.join(sorted(fams))}).",
        f"{len(primary)} primary document{'s' if len(primary) != 1 else ''} (company filings / exchange filings / regulator)." if primary else
        "No primary filing in the support set: grade capped.",
        f"Third-party record (regulator / counterparty) present: {', '.join(s.publisher for s in third_party_record)}." if third_party_record
        else "No regulator or counterparty document of its own in the support set (third-party facts arrive via press).",
        f"Skeptic: {len(survives)} checks survived, {len(weakens)} weakened ({', '.join(c.name for c in weakens) or 'none'}), "
        f"{len(refutes)} refuted.",
    ]
    if derived_inputs:
        reasons.append(f"{derived_inputs} input figure(s) are back-computed from reported growth rates (tolerance bands kept).")
    if led.conflicts:
        reasons.append(f"{len(led.conflicts)} source conflict(s) resolved by hierarchy; see audit log.")
    return grade, round(score, 3), reasons


def novelty(led: EvidenceLedger, h: Hypothesis) -> tuple[float, list[str]]:
    why: list[str] = []
    fams = led.families(h.support)
    nonco = [f for f in fams if f != "company"]
    s = 0.40
    s += min(0.30, 0.06 * len(nonco))
    why.append(f"joins {len(nonco)} non-company source families to the company's own disclosures")
    evs = [led.events[i] for i in h.events]
    third = [e for e in evs if e.actor not in ("company", None) and e.kind in ("regulatory", "policy")]
    own = [e for e in led.events if e.actor == "company" and e.kind in ("disclosure", "results")]
    if third and own:
        t0 = min(periods.parse_date(e.date) for e in third)
        firsts = [periods.parse_date(e.date) for e in own if periods.parse_date(e.date) >= t0]
        if firsts:
            lead = (min(firsts) - t0).days
            if lead > 0:
                s += 0.15
                why.append(f"a third-party record leads the company's own disclosure by {lead} days")
    incidents = [e for e in evs if "incident" in e.tags]
    if len(incidents) >= 2:
        s += 0.05
        why.append(f"links {len(incidents)} separately reported incidents into one pattern")
    if len(h.calcs) >= 3:
        s += 0.10
        why.append(f"rests on {len(h.calcs)} derived calculations no single source states")
    if any(c.name == "already_mainstream" and c.verdict == "weakens" for c in h.checks):
        s -= 0.20
        why.append("partly visible in headlines (penalised)")
    return round(max(0.0, min(1.0, s)), 3), why


def score(led: EvidenceLedger, h: Hypothesis) -> Hypothesis:
    grade, ev_score, reasons = evidence_strength(led, h)
    nov, nov_why = novelty(led, h)
    weakens = sum(1 for c in h.checks if c.verdict == "weakens")
    refutes = sum(1 for c in h.checks if c.verdict == "refutes")
    rob = max(0.0, 1.0 - 0.12 * weakens - 0.5 * refutes)
    h.scores.update(evidence=ev_score, novelty=nov, robustness=round(rob, 3))
    h.scores["total"] = round(sum(WEIGHTS[k] * h.scores.get(k, 0) for k in WEIGHTS), 3)
    h.facts["strength"] = {"grade": grade, "score": ev_score, "reasons": reasons}
    h.facts["novelty_why"] = nov_why
    led.log("score", h.id, f"total {h.scores['total']} (novelty {nov}, evidence {ev_score} [{grade}], "
            f"materiality {h.scores.get('materiality', 0):.2f}, robustness {rob:.2f})")
    return h
