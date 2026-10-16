"""Decide what is worth investigating.

Angles are scored by (a) prior fit to the company's archetype and (b) whether
the quantitative scan found an anomaly that the angle could explain. The plan
always keeps one slot for an open-ended angle proposed by the LLM, so the agent
can find a story the library does not anticipate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..analytics.scan import Anomaly
from ..knowledge import load_pack
from ..ledger import EvidenceLedger
from .profiler import CompanyProfile

# Which anomaly types make which angle more likely to pay off.
TRIGGERS = {
    "geo_mix_shift": {"mix_shift": 1.0, "segment_drop": 0.4},
    "replacement_concentration": {"mix_shift": 0.6, "segment_drop": 0.8, "segment_step_down": 0.6},
    "counterparty_regulatory": {"segment_step_down": 1.0, "segment_drop": 0.6},
    "own_site_regulatory": {"segment_drop": 0.5, "segment_step_down": 0.5},
    "specialty_pivot": {"mix_shift": 0.5},
    "payor_policy": {"growth_gap": 1.0, "specialty_share_shift": 0.8, "unit_price_stall": 0.8},
    "capacity_gap": {"capacity_gap": 1.0},
    "working_capital_divergence": {},
    "narrative_gap": {"growth_gap": 0.3},
}


@dataclass
class Task:
    angle: str
    brief: str
    question: str
    queries: list[str]
    priority: float
    reasons: list[str] = field(default_factory=list)


def _fmt(template: str, ctx: dict) -> str:
    class Safe(dict):
        def __missing__(self, k):
            return "{" + k + "}"
    return template.format_map(Safe(ctx))


def plan(led: EvidenceLedger, prof: CompanyProfile, anomalies: list[Anomaly], as_of_fy: int,
         max_tasks: int = 6) -> list[Task]:
    pack = load_pack()
    ctx = {"company": prof.name, "fy": f"{as_of_fy % 100:02d}", "fy_minus5": f"{(as_of_fy - 5) % 100:02d}",
           "fy_plus2": f"{(as_of_fy + 2) % 100:02d}", "fy_label": f"FY{(as_of_fy - 1) % 100:02d}",
           "q_label": f"Q1FY{as_of_fy % 100:02d}"}
    drops = [a for a in anomalies if a.type in ("segment_drop", "segment_step_down", "mix_shift") and a.magnitude < 0]
    if drops:
        ctx["segment"] = max(drops, key=lambda a: abs(a.magnitude)).subject
    if prof.counterparties:
        ctx["counterparty"] = prof.counterparties[0]
    products = [ln.product for ln in led.links if ln.product]
    if products:
        ctx["product"] = products[0]
    shifts = [a for a in anomalies if a.type == "specialty_share_shift"]
    if shifts:
        ctx["specialty"] = shifts[0].subject

    tasks: list[Task] = []
    for ang in pack["angles"]:
        fit = 1.0 if prof.archetype in ang["archetypes"] else (0.25 if any(
            prof.archetype_scores.get(a, 0) > 0.2 for a in ang["archetypes"]) else 0.0)
        if fit == 0:
            continue
        reasons = [f"archetype fit {fit:.2f}"]
        trig = 0.0
        for a in anomalies:
            w = TRIGGERS.get(ang["id"], {}).get(a.type, 0)
            if w:
                trig = max(trig, w)
                reasons.append(f"triggered by {a.type}: {a.description[:90]}")
        pr = 0.4 * fit + 0.6 * trig
        if pr <= 0.1:
            continue
        tasks.append(Task(ang["id"], ang["brief"], ang["question"], [_fmt(q, ctx) for q in ang["queries"]], round(pr, 3), reasons))
    tasks.sort(key=lambda t: -t.priority)
    tasks = tasks[:max_tasks]
    for t in tasks:
        led.log("plan", f"angle:{t.angle}", f"priority {t.priority} [{t.brief}] {t.question}", [])
    return tasks
